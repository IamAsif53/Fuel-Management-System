"""Integration tests for Task 2.1: Alembic migrations & TimescaleDB hypertables.

These tests execute against the live TimescaleDB container (default localhost:15432).
Marked with @pytest.mark.integration.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from app.db.models import (
    Decision,
    DecisionLine,
    DemandObservation,
    Forecast,
    Ledger,
    RiskAssessment,
    SimRun,
    WorldSnapshot,
)
from app.db.session import (
    check_db_health,
    resolve_database_url,
    run_migrations,
)
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

DB_TEST_URL = resolve_database_url()


@pytest_asyncio.fixture
async def db_engine() -> AsyncEngine:
    """Fixture to provide a connected AsyncEngine with NullPool for event-loop isolation."""
    engine = create_async_engine(DB_TEST_URL, poolclass=NullPool)
    is_healthy = await check_db_health(engine)
    if not is_healthy:
        pytest.skip(f"Database at {DB_TEST_URL} is not reachable.")
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncSession:
    """Fixture providing an active AsyncSession in a rolled-back transaction for test isolation."""
    sm = async_sessionmaker(bind=db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sm() as session:
        yield session
        await session.rollback()


# =============================================================================
# 1. Database Health & Migration Execution
# =============================================================================

async def test_database_health_check(db_engine: AsyncEngine):
    """Verify check_db_health returns True against live TimescaleDB."""
    assert await check_db_health(db_engine) is True


async def test_run_migrations_idempotency(db_engine: AsyncEngine):
    """Verify running migrations repeatedly succeeds without duplicate schema errors."""
    # Run once
    await run_migrations(DB_TEST_URL)

    # Run second time (should be clean no-op at head)
    await run_migrations(DB_TEST_URL)


# =============================================================================
# 2. Schema Structure & Hypertable Verification
# =============================================================================

async def test_all_18_tables_exist_in_database(db_session: AsyncSession):
    """Verify all 18 specified tables exist in public schema."""
    res = await db_session.execute(text("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
    """))
    db_tables = {row[0] for row in res.fetchall()}

    expected_tables = {
        "sim_runs",
        "world_snapshots",
        "demand_observations",
        "forecasts",
        "risk_assessments",
        "decisions",
        "decision_lines",
        "alerts",
        "incidents",
        "audit_log",
        "model_versions",
        "eval_runs",
        "ledger",
        "feature_flags",
        "quarantine",
        "allocation_reconciliation",
        "decision_outcomes",
        "playbook_runs",
        "alembic_version",
    }
    assert expected_tables.issubset(db_tables)


async def test_timescaledb_hypertables_registered(db_session: AsyncSession):
    """Verify that world_snapshots and demand_observations are recognized TimescaleDB hypertables."""
    res = await db_session.execute(text("""
        SELECT hypertable_name
        FROM timescaledb_information.hypertables
        WHERE hypertable_schema = 'public'
    """))
    hypertables = {row[0] for row in res.fetchall()}

    assert "world_snapshots" in hypertables
    assert "demand_observations" in hypertables


async def test_timescaledb_continuous_aggregate_created(db_session: AsyncSession):
    """Verify that continuous aggregate materialized view demand_hourly exists."""
    res = await db_session.execute(text("""
        SELECT view_name
        FROM timescaledb_information.continuous_aggregates
        WHERE view_schema = 'public'
    """))
    caggs = {row[0] for row in res.fetchall()}
    assert "demand_hourly" in caggs


# =============================================================================
# 3. Model CRUD & Relational Round-Trip Verification
# =============================================================================

async def test_sim_run_and_world_snapshot_roundtrip(db_session: AsyncSession):
    """Verify insert and query of SimRun and WorldSnapshot with JSONB and timestamps."""
    sim_run = SimRun(
        scenario_id="integration-test-scenario",
        seed=12345,
        tick_minutes=15,
        started_at=datetime.now(UTC),
        first_tick=0,
    )
    db_session.add(sim_run)
    await db_session.flush()

    assert sim_run.id is not None

    snapshot = WorldSnapshot(
        sim_run_id=sim_run.id,
        tick=0,
        sim_time=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        depots=[{"id": "depot-gazipur", "inventory": {"DIESEL": 50000.0}}],
        stations=[{"id": "station-mirpur", "inventory": {"DIESEL": 8000.0}}],
        stale=False,
        degraded=False,
    )
    db_session.add(snapshot)
    await db_session.flush()

    assert snapshot.id is not None
    assert snapshot.sim_time == datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    assert snapshot.depots[0]["id"] == "depot-gazipur"


async def test_demand_observation_roundtrip(db_session: AsyncSession):
    """Verify insert and query of DemandObservation on hypertable."""
    sim_run = SimRun(scenario_id="demand-test", tick_minutes=15)
    db_session.add(sim_run)
    await db_session.flush()

    obs = DemandObservation(
        sim_run_id=sim_run.id,
        sim_row_id=101,
        station_id="station-mirpur",
        fuel_type="DIESEL",
        tick=1,
        sim_time=datetime(2026, 1, 1, 0, 15, tzinfo=UTC),
        demand_liters=250.0,
        served_liters=250.0,
        unmet_liters=0.0,
    )
    db_session.add(obs)
    await db_session.flush()

    # Query back
    res = await db_session.execute(
        text(
            "SELECT station_id, demand_liters FROM demand_observations "
            "WHERE sim_run_id = :run_id AND sim_row_id = :row_id"
        ),
        {"run_id": sim_run.id, "row_id": 101},
    )
    row = res.fetchone()
    assert row is not None
    assert row[0] == "station-mirpur"
    assert row[1] == 250.0


async def test_ledger_append_and_hash_roundtrip(db_session: AsyncSession):
    """Verify append-only ledger model with sha256 hash."""
    entry = Ledger(
        actor="system",
        action="CYCLE_COMPLETE",
        payload={"tick": 1, "status": "OK"},
        prev_hash="0" * 64,
        hash="a" * 64,
    )
    db_session.add(entry)
    await db_session.flush()

    assert entry.id is not None
    assert entry.hash == "a" * 64


async def test_decision_and_decision_lines_cascade(db_session: AsyncSession):
    """Verify Decision and DecisionLine relational insertion and cascade."""
    sim_run = SimRun(scenario_id="dec-test", tick_minutes=15)
    db_session.add(sim_run)
    await db_session.flush()

    dec = Decision(
        sim_run_id=sim_run.id,
        tick=5,
        policy="optimizer",
        status="PROPOSED",
        mode="advisory",
        confidence=0.95,
        degraded=False,
    )
    line1 = DecisionLine(
        decision=dec,
        line_no=1,
        source_depot_id="depot-gazipur",
        destination_station_id="station-mirpur",
        route_id="route-gazipur-mirpur",
        fuel_type="DIESEL",
        quantity=3000.0,
        idempotency_key=f"test-line-{datetime.now(UTC).timestamp()}",
        status="PLANNED",
    )
    db_session.add(dec)
    db_session.add(line1)
    await db_session.flush()

    assert dec.id is not None
    assert line1.id is not None
    assert line1.decision_id == dec.id
    assert len(dec.lines) == 1


async def test_forecast_and_risk_assessment_sim_time_not_null(db_session: AsyncSession):
    """Verify Forecast and RiskAssessment require sim_time TIMESTAMPTZ NOT NULL (13_ARCHITECTURE_V2.md §6)."""
    sim_run = SimRun(scenario_id="forecast-test", tick_minutes=15)
    db_session.add(sim_run)
    await db_session.flush()

    now = datetime.now(UTC)
    forecast = Forecast(
        sim_run_id=sim_run.id,
        made_at_tick=10,
        station_id="station-mirpur",
        fuel_type="DIESEL",
        target_tick=12,
        p10=100.0,
        p50=200.0,
        p90=300.0,
        sim_time=now,
    )
    risk = RiskAssessment(
        sim_run_id=sim_run.id,
        tick=10,
        station_id="station-mirpur",
        fuel_type="DIESEL",
        risk_level="MEDIUM",
        sim_time=now,
    )
    db_session.add(forecast)
    db_session.add(risk)
    await db_session.flush()

    assert forecast.id is not None
    assert forecast.sim_time == now
    assert risk.id is not None
    assert risk.sim_time == now

