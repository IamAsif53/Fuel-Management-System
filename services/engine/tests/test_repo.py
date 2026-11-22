"""Integration tests for Task 2.2: Asynchronous Database Repository Layer (repo.py).

Verifies all repository functions against the live TimescaleDB container, testing:
- Creation and closure of simulation runs
- Snapshot upsert with conflict handling
- Demand observations insertion with ON CONFLICT DO NOTHING
- Forecast and risk projection insertion
- Decision creation with multiple lines and atomic relationship mapping
- Decision status and line tracking updates (including aliases)
- Decision filtering and retrieval
- Alert upsert, open-alert fingerprint deduplication, and resolution
- Incident lifecycle, timeline event ordering, and resolution
- Audit log persistence
- Tamper-evident ledger hash chaining and verification
- Quarantine, reconciliation, and outcome persistence
- Missing record handling
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from app.db.models import (
    Alert,
    AuditLog,
    DemandObservation,
    WorldSnapshot,
)
from app.db.repo import (
    append_incident_event,
    audit,
    close_sim_run,
    create_decision,
    create_sim_run,
    get_decision,
    insert_alert,
    insert_decision,
    insert_demand_rows,
    insert_forecasts,
    insert_incident,
    insert_outcome,
    insert_quarantine,
    insert_reconciliation,
    insert_risk_rows,
    ledger_append,
    list_decisions,
    resolve_alert,
    resolve_incident,
    update_decision_status,
    update_line,
    update_line_status,
    upsert_alert,
    upsert_snapshot,
    verify_ledger,
)
from app.db.session import check_db_health, resolve_database_url
from sqlalchemy import NullPool, select
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
    """Fixture providing an isolated AsyncEngine connected to the live TimescaleDB container."""
    engine = create_async_engine(DB_TEST_URL, poolclass=NullPool)
    is_healthy = await check_db_health(engine)
    if not is_healthy:
        pytest.skip(f"Database at {DB_TEST_URL} is not reachable.")
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncSession:
    """Fixture providing a transactional AsyncSession rolled back after each test."""
    sm = async_sessionmaker(bind=db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sm() as session:
        yield session
        await session.rollback()


# =============================================================================
# 1. SimRun Operations
# =============================================================================

async def test_sim_run_create_and_close(db_session: AsyncSession):
    """Verify sim_run creation, field population, closure, and missing ID handling."""
    start_time = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    sim_run = await create_sim_run(
        db_session,
        scenario_id="scenario-crisis",
        seed=12345,
        tick_minutes=15,
        started_at=start_time,
        first_tick=0,
    )
    assert sim_run.id is not None
    assert sim_run.scenario_id == "scenario-crisis"
    assert sim_run.seed == 12345
    assert sim_run.started_at == start_time
    assert sim_run.ended_at is None

    # Close run
    end_time = datetime(2026, 1, 1, 16, 0, tzinfo=UTC)
    closed = await close_sim_run(db_session, sim_run.id, ended_at=end_time, last_tick=96)
    assert closed is not None
    assert closed.id == sim_run.id
    assert closed.ended_at == end_time
    assert closed.last_tick == 96

    # Close non-existent run returns None
    missing = await close_sim_run(db_session, 999_999_999)
    assert missing is None


# =============================================================================
# 2. WorldSnapshot Operations
# =============================================================================

async def test_upsert_snapshot_insert_and_conflict_update(db_session: AsyncSession):
    """Verify snapshot insertion and in-place conflict update on (sim_run_id, tick, sim_time)."""
    sim_run = await create_sim_run(db_session, scenario_id="snap-test")
    sim_time = datetime(2026, 1, 1, 12, 15, tzinfo=UTC)

    # Initial snapshot
    snap_data = {
        "sim_run_id": sim_run.id,
        "tick": 1,
        "sim_time": sim_time,
        "depots": {"depot-1": {"fuel": 50000}},
        "stations": {"station-1": {"fuel": 10000}},
        "metrics": {"kpi": 0.85},
        "stale": False,
        "degraded": False,
    }
    s1 = await upsert_snapshot(db_session, snap_data)
    assert s1.id is not None
    assert s1.tick == 1
    assert s1.metrics == {"kpi": 0.85}
    assert s1.stale is False

    # Update snapshot for same tick & sim_time
    update_data = {
        "sim_run_id": sim_run.id,
        "tick": 1,
        "sim_time": sim_time,
        "depots": {"depot-1": {"fuel": 45000}},
        "stations": {"station-1": {"fuel": 15000}},
        "metrics": {"kpi": 0.95},
        "stale": True,
        "degraded": False,
    }
    s2 = await upsert_snapshot(db_session, update_data)
    assert s2.id == s1.id
    assert s2.metrics == {"kpi": 0.95}
    assert s2.stale is True

    # Verify directly via query
    res = await db_session.execute(
        select(WorldSnapshot).where(WorldSnapshot.sim_run_id == sim_run.id, WorldSnapshot.tick == 1)
    )
    snaps = list(res.scalars().all())
    assert len(snaps) == 1
    assert snaps[0].metrics == {"kpi": 0.95}


# =============================================================================
# 3. Demand Observations Operations
# =============================================================================

async def test_insert_demand_rows_and_conflict_do_nothing(db_session: AsyncSession):
    """Verify demand row insertion and ON CONFLICT DO NOTHING on composite PK."""
    sim_run = await create_sim_run(db_session, scenario_id="demand-test")
    sim_time = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    rows = [
        {
            "sim_run_id": sim_run.id,
            "sim_row_id": 101,
            "station_id": "station-mirpur",
            "fuel_type": "DIESEL",
            "tick": 0,
            "sim_time": sim_time,
            "demand_liters": 500.0,
            "served_liters": 500.0,
            "unmet_liters": 0.0,
        },
        {
            "sim_run_id": sim_run.id,
            "sim_row_id": 102,
            "station_id": "station-uttara",
            "fuel_type": "OCTANE",
            "tick": 0,
            "sim_time": sim_time,
            "demand_liters": 300.0,
            "served_liters": 250.0,
            "unmet_liters": 50.0,
        },
    ]

    count1 = await insert_demand_rows(db_session, rows)
    assert count1 == 2

    # Query back
    res = await db_session.execute(
        select(DemandObservation).where(DemandObservation.sim_run_id == sim_run.id)
    )
    db_rows = list(res.scalars().all())
    assert len(db_rows) == 2

    # Insert identical rows again -> ON CONFLICT DO NOTHING (no error, no duplicates)
    count2 = await insert_demand_rows(db_session, rows)
    assert count2 in (0, 2)  # depending on rowcount reporting, no error thrown

    res2 = await db_session.execute(
        select(DemandObservation).where(DemandObservation.sim_run_id == sim_run.id)
    )
    assert len(list(res2.scalars().all())) == 2


# =============================================================================
# 4. Forecast & Risk Assessment Operations
# =============================================================================

async def test_insert_forecasts_and_risk_rows(db_session: AsyncSession):
    """Verify batch insertion of Forecast and RiskAssessment records with non-null sim_time."""
    sim_run = await create_sim_run(db_session, scenario_id="pred-test")
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    forecast_items = [
        {
            "sim_run_id": sim_run.id,
            "made_at_tick": 4,
            "station_id": "station-mirpur",
            "fuel_type": "DIESEL",
            "target_tick": 6,
            "p10": 100.0,
            "p50": 200.0,
            "p90": 300.0,
            "sim_time": now,
        },
        {
            "sim_run_id": sim_run.id,
            "made_at_tick": 4,
            "station_id": "station-uttara",
            "fuel_type": "OCTANE",
            "target_tick": 6,
            "p10": 150.0,
            "p50": 250.0,
            "p90": 350.0,
            "sim_time": now,
        },
    ]
    created_forecasts = await insert_forecasts(db_session, forecast_items)
    assert len(created_forecasts) == 2
    assert created_forecasts[0].id is not None

    risk_items = [
        {
            "sim_run_id": sim_run.id,
            "tick": 4,
            "station_id": "station-mirpur",
            "fuel_type": "DIESEL",
            "risk_level": "HIGH",
            "stockout_prob": 0.85,
            "sim_time": now,
        }
    ]
    created_risks = await insert_risk_rows(db_session, risk_items)
    assert len(created_risks) == 1
    assert created_risks[0].id is not None
    assert created_risks[0].risk_level == "HIGH"


# =============================================================================
# 5. Decision & DecisionLine Operations
# =============================================================================

async def test_decision_lifecycle_and_line_updates(db_session: AsyncSession):
    """Verify Decision creation with multiple lines, get_decision, line updates, and aliases."""
    sim_run = await create_sim_run(db_session, scenario_id="dec-test")

    decision_data = {
        "sim_run_id": sim_run.id,
        "tick": 10,
        "policy": "optimizer",
        "policy_version": "v1.0",
        "trigger": "scheduled",
        "status": "PROPOSED",
        "mode": "advisory",
        "confidence": 0.92,
    }
    line_data = [
        {
            "line_no": 1,
            "source_depot_id": "depot-gazipur",
            "destination_station_id": "station-mirpur",
            "route_id": "route-gazipur-mirpur",
            "fuel_type": "DIESEL",
            "quantity": 4000.0,
            "idempotency_key": "key-line-1",
            "status": "PLANNED",
            "expected_arrival_tick": 12,
        },
        {
            "line_no": 2,
            "source_depot_id": "depot-fatullah",
            "destination_station_id": "station-dhanmondi",
            "route_id": "route-fatullah-dhanmondi",
            "fuel_type": "OCTANE",
            "quantity": 3000.0,
            "idempotency_key": "key-line-2",
            "status": "PLANNED",
            "expected_arrival_tick": 13,
        },
    ]

    dec = await create_decision(db_session, decision_data, line_data)
    assert dec.id is not None
    assert len(dec.lines) == 2
    line1 = dec.lines[0]
    assert line1.id is not None
    assert line1.decision_id == dec.id

    # Retrieve via get_decision
    loaded = await get_decision(db_session, dec.id)
    assert loaded is not None
    assert len(loaded.lines) == 2
    assert loaded.lines[0].destination_station_id == "station-mirpur"

    # Update decision status
    now = datetime(2026, 1, 1, 14, 0, tzinfo=UTC)
    updated_dec = await update_decision_status(
        db_session,
        dec.id,
        status="APPROVED",
        approved_by="operator-asif",
        approved_at=now,
    )
    assert updated_dec is not None
    assert updated_dec.status == "APPROVED"
    assert updated_dec.approved_by == "operator-asif"

    # Update decision line using alias
    updated_line = await update_line_status(
        db_session,
        line1.id,
        status="IN_TRANSIT",
        sim_allocation_id=888,
        actual_arrival_tick=12,
        received_liters=4000.0,
    )
    assert updated_line is not None
    assert updated_line.status == "IN_TRANSIT"
    assert updated_line.sim_allocation_id == 888

    # Query missing decision / line returns None
    assert await get_decision(db_session, 999_999) is None
    assert await update_decision_status(db_session, 999_999, "APPROVED") is None
    assert await update_line(db_session, 999_999, status="FAILED") is None


async def test_list_decisions_filtering(db_session: AsyncSession):
    """Verify decision listing with filters for sim_run_id, status, policy, and from_tick."""
    run1 = await create_sim_run(db_session, scenario_id="run-1")
    run2 = await create_sim_run(db_session, scenario_id="run-2")

    await insert_decision(db_session, {"sim_run_id": run1.id, "tick": 5, "policy": "optimizer", "status": "PROPOSED"})
    await insert_decision(db_session, {"sim_run_id": run1.id, "tick": 10, "policy": "heuristic", "status": "EXECUTED"})
    await insert_decision(db_session, {"sim_run_id": run2.id, "tick": 8, "policy": "optimizer", "status": "EXECUTED"})

    # Filter by sim_run_id
    r1_list = await list_decisions(db_session, sim_run_id=run1.id)
    assert len(r1_list) == 2

    # Filter by policy
    opt_list = await list_decisions(db_session, policy="optimizer")
    assert len(opt_list) == 2

    # Filter by status
    exec_list = await list_decisions(db_session, status="EXECUTED")
    assert len(exec_list) == 2

    # Filter by from_tick
    tick_list = await list_decisions(db_session, from_tick=8)
    assert len(tick_list) == 2


# =============================================================================
# 6. Alert Operations
# =============================================================================

async def test_alert_upsert_dedup_and_resolve(db_session: AsyncSession):
    """Verify alert upsert enforces open-alert fingerprint deduplication, and resolution."""
    fp = "stockout:station-mirpur:DIESEL"

    # 1. Create first open alert
    a1 = await upsert_alert(
        db_session,
        {
            "fingerprint": fp,
            "category": "STOCKOUT_RISK",
            "severity": "WARNING",
            "title": "Stockout Risk",
            "message": "Projected fill < 0.6",
            "status": "OPEN",
            "opened_tick": 5,
        },
    )
    assert a1.id is not None
    assert a1.severity == "WARNING"

    # 2. Upsert same fingerprint -> updates existing open alert (no duplicate row)
    a2 = await insert_alert(
        db_session,
        {
            "fingerprint": fp,
            "severity": "CRITICAL",
            "message": "Stockout imminent in 2 hours",
            "status": "OPEN",
            "opened_tick": 6,
        },
    )
    assert a2.id == a1.id
    assert a2.severity == "CRITICAL"
    assert a2.message == "Stockout imminent in 2 hours"

    # Verify only 1 open alert exists
    res = await db_session.execute(select(Alert).where(Alert.fingerprint == fp))
    assert len(list(res.scalars().all())) == 1

    # 3. Resolve alert
    resolved = await resolve_alert(db_session, fingerprint=fp, resolved_tick=10)
    assert resolved is not None
    assert resolved.status == "RESOLVED"
    assert resolved.resolved_tick == 10
    assert resolved.resolved_at is not None

    # 4. Now upsert same fingerprint -> creates NEW open alert since previous is RESOLVED
    a3 = await upsert_alert(
        db_session,
        {
            "fingerprint": fp,
            "category": "STOCKOUT_RISK",
            "severity": "WARNING",
            "title": "New Risk",
            "status": "OPEN",
            "opened_tick": 15,
        },
    )
    assert a3.id != a1.id
    assert a3.status == "OPEN"

    # Resolve by alert_id
    res_by_id = await resolve_alert(db_session, alert_id=a3.id)
    assert res_by_id is not None
    assert res_by_id.status == "RESOLVED"

    # Non-existent alert returns None
    assert await resolve_alert(db_session, alert_id=999_999) is None


# =============================================================================
# 7. Incident Operations
# =============================================================================

async def test_incident_lifecycle_and_timeline_order(db_session: AsyncSession):
    """Verify incident opening, chronological timeline event appends, and resolution."""
    inc = await insert_incident(
        db_session,
        kind="SIMULATOR_DISCONNECT",
        component="SimClient",
        summary="SSE connection watchdog triggered reconnect",
    )
    assert inc.id is not None
    assert inc.status == "OPEN"
    assert inc.timeline == []

    # Append timeline events in sequence
    e1 = await append_incident_event(db_session, inc.id, "Watchdog timeout after 30s")
    assert e1 is not None
    assert len(e1.timeline) == 1
    assert e1.timeline[0]["event"] == "Watchdog timeout after 30s"

    e2 = await append_incident_event(
        db_session,
        inc.id,
        {"event": "Reconnecting with backoff", "attempt": 1, "at": "2026-01-01T12:05:00Z"},
    )
    assert e2 is not None
    assert len(e2.timeline) == 2
    assert e2.timeline[1]["event"] == "Reconnecting with backoff"

    # Resolve incident
    res_time = datetime(2026, 1, 1, 12, 10, tzinfo=UTC)
    resolved = await resolve_incident(db_session, inc.id, resolved_at=res_time, summary="Stream restored")
    assert resolved is not None
    assert resolved.status == "RESOLVED"
    assert resolved.resolved_at == res_time
    assert resolved.summary == "Stream restored"

    # Non-existent incident returns None
    assert await append_incident_event(db_session, 999_999, "test") is None
    assert await resolve_incident(db_session, 999_999) is None


# =============================================================================
# 8. Audit Log Operations
# =============================================================================

async def test_audit_log_entry_persistence(db_session: AsyncSession):
    """Verify audit log row insertion."""
    entry = await audit(
        db_session,
        actor="operator-1",
        action="MANUAL_ALLOCATION",
        entity_type="decision",
        entity_id="dec-42",
        payload={"quantity": 5000, "route": "route-1"},
    )
    assert entry.id is not None
    assert entry.actor == "operator-1"
    assert entry.action == "MANUAL_ALLOCATION"
    assert entry.entity_id == "dec-42"
    assert entry.at is not None

    res = await db_session.execute(select(AuditLog).where(AuditLog.id == entry.id))
    persisted = res.scalar_one()
    assert persisted.payload == {"quantity": 5000, "route": "route-1"}


# =============================================================================
# 9. Tamper-Evident Ledger Operations
# =============================================================================

async def test_ledger_append_and_hash_chain_verification(db_session: AsyncSession):
    """Verify ledger append-only hash chaining starting from GENESIS and chain verification."""
    # Ensure empty ledger in this test transaction
    e1 = await ledger_append(
        db_session,
        actor="system",
        action="INITIALIZE",
        payload={"tick": 0},
    )
    assert e1.id is not None
    assert e1.prev_hash == "GENESIS"
    assert len(e1.hash) == 64

    e2 = await ledger_append(
        db_session,
        actor="optimizer",
        action="DECISION_PROPOSED",
        payload={"decision_id": 10},
    )
    assert e2.prev_hash == e1.hash
    assert len(e2.hash) == 64

    e3 = await ledger_append(
        db_session,
        actor="operator",
        action="DECISION_APPROVED",
        payload={"decision_id": 10, "by": "user-1"},
    )
    assert e3.prev_hash == e2.hash

    # Verify ledger integrity
    status = await verify_ledger(db_session)
    assert status["ok"] is True
    assert status["length"] >= 3
    assert status["head"] == e3.hash


# =============================================================================
# 10. Quarantine, Reconciliation & Outcome Operations
# =============================================================================

async def test_quarantine_reconciliation_and_outcome_persistence(db_session: AsyncSession):
    """Verify Quarantine, AllocationReconciliation, and DecisionOutcome repository operations."""
    sim_run = await create_sim_run(db_session, scenario_id="v2-test")

    # Quarantine
    q = await insert_quarantine(
        db_session,
        resource="allocations",
        errors={"code": "MALFORMED_PAYLOAD", "detail": "Missing fuel_type"},
        raw_body="{'depot_id': 1}",
        sim_tick=12,
    )
    assert q.id is not None
    assert q.resource == "allocations"
    assert q.sim_tick == 12

    # Decision & Line for reconciliation & outcome
    dec = await create_decision(
        db_session,
        {"sim_run_id": sim_run.id, "tick": 12, "policy": "optimizer", "status": "EXECUTED"},
        [
            {
                "line_no": 1,
                "source_depot_id": "depot-1",
                "destination_station_id": "station-1",
                "route_id": "route-1",
                "fuel_type": "DIESEL",
                "quantity": 5000.0,
                "idempotency_key": "rec-key-1",
                "status": "ARRIVED",
            }
        ],
    )
    line = dec.lines[0]

    # Reconciliation insert & update
    rec = await insert_reconciliation(
        db_session,
        decision_line_id=line.id,
        sent=5000.0,
        received=4800.0,
        loss=200.0,
        loss_reason="OVERFLOW",
        method="audit",
    )
    assert rec.decision_line_id == line.id
    assert rec.loss == 200.0
    assert rec.created_at is not None

    # Update reconciliation
    rec_updated = await insert_reconciliation(
        db_session,
        decision_line_id=line.id,
        loss=250.0,
    )
    assert rec_updated.loss == 250.0

    # Decision Outcome insert & update
    outcome = await insert_outcome(
        db_session,
        decision_id=dec.id,
        evaluated_tick=16,
        predicted={"stockouts_avoided": 1},
        realised={"stockouts_avoided": 1},
        hit=True,
    )
    assert outcome.decision_id == dec.id
    assert outcome.hit is True
    assert outcome.created_at is not None
