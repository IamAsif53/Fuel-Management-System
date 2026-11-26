"""Asynchronous database repository layer for engine (Task 2.2).

Follows 23_MODULE_SPECS.md §B8 and PHASE_02_PERSISTENCE_AND_API.md Task 2.2.
All functions accept an AsyncSession explicitly and do not manage transaction
commit/rollback boundaries internally, allowing the caller (or write buffer)
to manage atomic transactional scopes.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    Alert,
    AllocationReconciliation,
    AuditLog,
    Decision,
    DecisionLine,
    DecisionOutcome,
    DemandObservation,
    Forecast,
    Incident,
    Ledger,
    Quarantine,
    RiskAssessment,
    SimRun,
    WorldSnapshot,
)


def canonical_json(obj: Any) -> str:
    """Canonical JSON serialization for hash chaining (RFC 8785 compliant style)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def ledger_hash(prev_hash: str, body: dict[str, Any]) -> str:
    """Compute sha256 hash chaining for ledger entries."""
    return hashlib.sha256((prev_hash + canonical_json(body)).encode("utf-8")).hexdigest()


# =============================================================================
# 1. SimRun Operations
# =============================================================================

async def create_sim_run(
    session: AsyncSession,
    scenario_id: str | None = None,
    seed: int | None = None,
    tick_minutes: int | None = None,
    started_at: datetime | None = None,
    first_tick: int | None = None,
    last_tick: int | None = None,
) -> SimRun:
    """Create and persist a new simulator run tracking record."""
    sim_run = SimRun(
        scenario_id=scenario_id,
        seed=seed,
        tick_minutes=tick_minutes,
        started_at=started_at or datetime.now(UTC),
        first_tick=first_tick,
        last_tick=last_tick,
    )
    session.add(sim_run)
    await session.flush()
    return sim_run


async def close_sim_run(
    session: AsyncSession,
    sim_run_id: int,
    ended_at: datetime | None = None,
    last_tick: int | None = None,
) -> SimRun | None:
    """Close an existing simulator run record upon reset or completion."""
    stmt = select(SimRun).where(SimRun.id == sim_run_id).with_for_update()
    res = await session.execute(stmt)
    sim_run = res.scalar_one_or_none()
    if not sim_run:
        return None
    sim_run.ended_at = ended_at or datetime.now(UTC)
    if last_tick is not None:
        sim_run.last_tick = last_tick
    await session.flush()
    return sim_run


# =============================================================================
# 2. WorldSnapshot Operations
# =============================================================================

async def upsert_snapshot(
    session: AsyncSession,
    snapshot: WorldSnapshot | dict[str, Any],
) -> WorldSnapshot:
    """Insert or update a world snapshot record for a processed tick."""
    if isinstance(snapshot, WorldSnapshot):
        vals = {
            "sim_run_id": snapshot.sim_run_id,
            "tick": snapshot.tick,
            "sim_time": snapshot.sim_time,
            "depots": snapshot.depots,
            "stations": snapshot.stations,
            "routes": snapshot.routes,
            "events": snapshot.events,
            "supply": snapshot.supply,
            "metrics": snapshot.metrics,
            "stale": snapshot.stale,
            "degraded": snapshot.degraded,
        }
    else:
        vals = dict(snapshot)

    stmt = pg_insert(WorldSnapshot).values(vals)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_world_snapshots_sim_run_tick_time",
        set_={
            "depots": stmt.excluded.depots,
            "stations": stmt.excluded.stations,
            "routes": stmt.excluded.routes,
            "events": stmt.excluded.events,
            "supply": stmt.excluded.supply,
            "metrics": stmt.excluded.metrics,
            "stale": stmt.excluded.stale,
            "degraded": stmt.excluded.degraded,
        },
    ).returning(WorldSnapshot.id, WorldSnapshot.sim_time)

    res = await session.execute(stmt)
    snapshot_id, sim_time = res.fetchone()
    await session.flush()
    loaded = await session.get(WorldSnapshot, (snapshot_id, sim_time), populate_existing=True)
    return loaded  # type: ignore[return-value]


# =============================================================================
# 3. Demand Observations Operations
# =============================================================================

async def insert_demand_rows(
    session: AsyncSession,
    rows: Sequence[DemandObservation | dict[str, Any]],
) -> int:
    """Insert demand observations using ON CONFLICT DO NOTHING on composite primary key."""
    if not rows:
        return 0

    row_dicts = []
    for r in rows:
        if isinstance(r, DemandObservation):
            row_dicts.append({
                "sim_run_id": r.sim_run_id,
                "sim_row_id": r.sim_row_id,
                "station_id": r.station_id,
                "fuel_type": r.fuel_type,
                "tick": r.tick,
                "sim_time": r.sim_time,
                "demand_liters": r.demand_liters,
                "served_liters": r.served_liters,
                "unmet_liters": r.unmet_liters,
            })
        else:
            row_dicts.append(dict(r))

    stmt = pg_insert(DemandObservation).values(row_dicts)
    stmt = stmt.on_conflict_do_nothing(
        index_elements=["sim_run_id", "sim_row_id", "sim_time"]
    )
    res = await session.execute(stmt)
    await session.flush()
    return res.rowcount if res.rowcount is not None and res.rowcount >= 0 else len(row_dicts)


# =============================================================================
# 4. Forecast & Risk Assessment Operations
# =============================================================================

async def insert_forecasts(
    session: AsyncSession,
    forecasts: Sequence[Forecast | dict[str, Any]],
) -> list[Forecast]:
    """Insert forecast prediction rows."""
    if not forecasts:
        return []

    models = []
    for f in forecasts:
        if isinstance(f, Forecast):
            models.append(f)
        else:
            models.append(Forecast(**f))

    session.add_all(models)
    await session.flush()
    return models


async def insert_risk_rows(
    session: AsyncSession,
    risk_rows: Sequence[RiskAssessment | dict[str, Any]],
) -> list[RiskAssessment]:
    """Insert risk assessment projection rows."""
    if not risk_rows:
        return []

    models = []
    for r in risk_rows:
        if isinstance(r, RiskAssessment):
            models.append(r)
        else:
            models.append(RiskAssessment(**r))

    session.add_all(models)
    await session.flush()
    return models


# =============================================================================
# 5. Decision & Line Operations
# =============================================================================

async def create_decision(
    session: AsyncSession,
    decision: Decision | dict[str, Any],
    lines: Sequence[DecisionLine | dict[str, Any]] | None = None,
) -> Decision:
    """Create a decision and its associated allocation lines atomically."""
    if isinstance(decision, Decision):
        dec_obj = decision
    else:
        dec_obj = Decision(**decision)

    if lines:
        for line in lines:
            if isinstance(line, DecisionLine):
                line_obj = line
            else:
                line_obj = DecisionLine(**line)
            dec_obj.lines.append(line_obj)

    session.add(dec_obj)
    await session.flush()
    return dec_obj

# Phase 02 compatibility alias
insert_decision = create_decision


async def update_decision_status(
    session: AsyncSession,
    decision_id: int,
    status: str,
    approved_by: str | None = None,
    approved_at: datetime | None = None,
    rejected_reason: str | None = None,
) -> Decision | None:
    """Update decision state and authorization details."""
    stmt = (
        select(Decision)
        .options(selectinload(Decision.lines))
        .where(Decision.id == decision_id)
        .with_for_update()
    )
    res = await session.execute(stmt)
    decision = res.scalar_one_or_none()
    if not decision:
        return None

    decision.status = status
    if approved_by is not None:
        decision.approved_by = approved_by
    if approved_at is not None:
        decision.approved_at = approved_at
    if rejected_reason is not None:
        decision.rejected_reason = rejected_reason

    await session.flush()
    return decision


async def update_line(
    session: AsyncSession,
    line_id: int,
    status: str | None = None,
    sim_allocation_id: int | None = None,
    sim_error_code: str | None = None,
    actual_arrival_tick: int | None = None,
    received_liters: float | None = None,
) -> DecisionLine | None:
    """Update shipment line status and simulator allocation tracking."""
    stmt = select(DecisionLine).where(DecisionLine.id == line_id).with_for_update()
    res = await session.execute(stmt)
    line = res.scalar_one_or_none()
    if not line:
        return None

    if status is not None:
        line.status = status
    if sim_allocation_id is not None:
        line.sim_allocation_id = sim_allocation_id
    if sim_error_code is not None:
        line.sim_error_code = sim_error_code
    if actual_arrival_tick is not None:
        line.actual_arrival_tick = actual_arrival_tick
    if received_liters is not None:
        line.received_liters = received_liters

    await session.flush()
    return line

# Phase 02 compatibility alias
update_line_status = update_line


async def get_decision(
    session: AsyncSession,
    decision_id: int,
) -> Decision | None:
    """Retrieve a decision and eagerly load its shipment lines."""
    stmt = select(Decision).options(selectinload(Decision.lines)).where(Decision.id == decision_id)
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


async def list_decisions(
    session: AsyncSession,
    sim_run_id: int | None = None,
    status: str | None = None,
    policy: str | None = None,
    from_tick: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Decision]:
    """Query decisions with filtering, ordered by tick descending."""
    stmt = (
        select(Decision)
        .options(selectinload(Decision.lines))
        .order_by(Decision.tick.desc(), Decision.id.desc())
    )
    if sim_run_id is not None:
        stmt = stmt.where(Decision.sim_run_id == sim_run_id)
    if status is not None:
        stmt = stmt.where(Decision.status == status)
    if policy is not None:
        stmt = stmt.where(Decision.policy == policy)
    if from_tick is not None:
        stmt = stmt.where(Decision.tick >= from_tick)

    stmt = stmt.limit(limit).offset(offset)
    res = await session.execute(stmt)
    return list(res.scalars().all())


# =============================================================================
# 6. Alert Operations
# =============================================================================

async def upsert_alert(
    session: AsyncSession,
    alert: Alert | dict[str, Any],
) -> Alert:
    """Insert or update an alert, enforcing uniqueness on open alert fingerprints."""
    if isinstance(alert, Alert):
        fp = alert.fingerprint
        fields = {
            "sim_run_id": alert.sim_run_id,
            "fingerprint": alert.fingerprint,
            "category": alert.category,
            "severity": alert.severity,
            "title": alert.title,
            "message": alert.message,
            "entity_type": alert.entity_type,
            "entity_id": alert.entity_id,
            "fuel_type": alert.fuel_type,
            "status": alert.status,
            "opened_tick": alert.opened_tick,
            "resolved_tick": alert.resolved_tick,
            "acknowledged_by": alert.acknowledged_by,
            "data": alert.data,
            "related_decision_id": alert.related_decision_id,
        }
    else:
        fields = dict(alert)
        fp = fields["fingerprint"]

    # Search for an existing open alert with this fingerprint
    stmt = (
        select(Alert)
        .where(Alert.fingerprint == fp, Alert.status != "RESOLVED")
        .with_for_update()
    )
    res = await session.execute(stmt)
    existing = res.scalar_one_or_none()
    run_id = fields.get("sim_run_id")

    if existing and run_id is not None and existing.sim_run_id != run_id:
        # Close prior/stale run's open alert before opening a fresh alert for the new run
        existing.status = "RESOLVED"
        existing.resolved_at = datetime.now(UTC)
        await session.flush()
        existing = None

    if existing:
        for k, v in fields.items():
            if v is not None:
                setattr(existing, k, v)
        await session.flush()
        return existing
    else:
        if run_id is not None:
            await session.execute(
                text(
                    "INSERT INTO sim_runs (id, started_at) VALUES (:id, NOW()) ON CONFLICT (id) DO NOTHING"
                ),
                {"id": run_id},
            )
        new_alert = Alert(**fields)
        session.add(new_alert)
        await session.flush()
        return new_alert

# Phase 02 compatibility alias
insert_alert = upsert_alert


async def resolve_alert(
    session: AsyncSession,
    fingerprint: str | None = None,
    alert_id: int | None = None,
    resolved_tick: int | None = None,
    resolved_at: datetime | None = None,
) -> Alert | None:
    """Resolve an open alert by fingerprint or ID."""
    stmt = select(Alert).where(Alert.status != "RESOLVED").with_for_update()
    if alert_id is not None:
        stmt = stmt.where(Alert.id == alert_id)
    elif fingerprint is not None:
        stmt = stmt.where(Alert.fingerprint == fingerprint)
    else:
        raise ValueError("Must provide either alert_id or fingerprint to resolve_alert.")

    res = await session.execute(stmt)
    alert = res.scalar_one_or_none()
    if not alert:
        return None

    alert.status = "RESOLVED"
    alert.resolved_at = resolved_at or datetime.now(UTC)
    if resolved_tick is not None:
        alert.resolved_tick = resolved_tick

    await session.flush()
    return alert


async def resolve_stale_run_alerts(
    session: AsyncSession,
    active_sim_run_id: int,
    resolved_tick: int | None = None,
) -> int:
    """Resolve any non-resolved alerts belonging to previous simulation runs (or sim_run_id IS NULL)."""
    q = text(
        """
        UPDATE alerts
        SET status = 'RESOLVED',
            resolved_at = NOW(),
            resolved_tick = COALESCE(resolved_tick, :resolved_tick)
        WHERE status != 'RESOLVED'
          AND (sim_run_id IS NULL OR sim_run_id != :active_sim_run_id)
        """
    )
    res = await session.execute(
        q,
        {
            "active_sim_run_id": active_sim_run_id,
            "resolved_tick": resolved_tick,
        },
    )
    return int(res.rowcount or 0)


# =============================================================================
# 7. Incident Operations
# =============================================================================

async def open_incident(
    session: AsyncSession,
    kind: str,
    component: str,
    summary: str | None = None,
    timeline: list[Any] | None = None,
    started_at: datetime | None = None,
) -> Incident:
    """Create a new platform incident record."""
    incident = Incident(
        kind=kind,
        component=component,
        status="OPEN",
        summary=summary,
        timeline=timeline or [],
        started_at=started_at or datetime.now(UTC),
    )
    session.add(incident)
    await session.flush()
    return incident

# Phase 02 compatibility alias
insert_incident = open_incident


async def append_incident_event(
    session: AsyncSession,
    incident_id: int,
    event: dict[str, Any] | str,
) -> Incident | None:
    """Append a timeline entry to an incident, preserving event ordering."""
    stmt = select(Incident).where(Incident.id == incident_id).with_for_update()
    res = await session.execute(stmt)
    incident = res.scalar_one_or_none()
    if not incident:
        return None

    tl = list(incident.timeline or [])
    if isinstance(event, str):
        entry = {"at": datetime.now(UTC).isoformat(), "event": event}
    else:
        entry = dict(event)
        if "at" not in entry:
            entry["at"] = datetime.now(UTC).isoformat()

    tl.append(entry)
    incident.timeline = tl
    await session.flush()
    return incident


async def resolve_incident(
    session: AsyncSession,
    incident_id: int,
    resolved_at: datetime | None = None,
    summary: str | None = None,
) -> Incident | None:
    """Resolve an incident and record resolution timestamp."""
    stmt = select(Incident).where(Incident.id == incident_id).with_for_update()
    res = await session.execute(stmt)
    incident = res.scalar_one_or_none()
    if not incident:
        return None

    incident.status = "RESOLVED"
    incident.resolved_at = resolved_at or datetime.now(UTC)
    if summary is not None:
        incident.summary = summary

    await session.flush()
    return incident


# =============================================================================
# 8. Audit Log Operations
# =============================================================================

async def audit(
    session: AsyncSession,
    actor: str | None,
    action: str | None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    payload: dict[str, Any] | None = None,
    at: datetime | None = None,
) -> AuditLog:
    """Log an operator action to the audit_log table."""
    log_entry = AuditLog(
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload,
        at=at or datetime.now(UTC),
    )
    session.add(log_entry)
    await session.flush()
    return log_entry


# =============================================================================
# 9. Tamper-Evident Ledger Operations
# =============================================================================

async def ledger_append(
    session: AsyncSession,
    actor: str,
    action: str,
    payload: dict[str, Any],
    at: datetime | None = None,
) -> Ledger:
    """Append a tamper-evident hash-chained record to the ledger."""
    # Obtain PostgreSQL advisory transaction lock 42 to serialize ledger appends
    try:
        await session.execute(text("SELECT pg_advisory_xact_lock(42);"))
    except Exception:
        pass

    # Query latest hash
    stmt = select(Ledger.hash).order_by(Ledger.id.desc()).limit(1).with_for_update()
    res = await session.execute(stmt)
    prev_hash = res.scalar_one_or_none() or "GENESIS"

    # Get sequence nextval for ledger id
    seq_res = await session.execute(text("SELECT nextval(pg_get_serial_sequence('ledger', 'id'));"))
    next_id = seq_res.scalar_one()

    entry_at = at or datetime.now(UTC)
    body = {
        "id": next_id,
        "at": entry_at.isoformat() if hasattr(entry_at, "isoformat") else str(entry_at),
        "actor": actor,
        "action": action,
        "payload": payload,
    }
    new_hash = ledger_hash(prev_hash, body)

    entry = Ledger(
        id=next_id,
        at=entry_at,
        actor=actor,
        action=action,
        payload=payload,
        prev_hash=prev_hash,
        hash=new_hash,
    )
    session.add(entry)
    await session.flush()
    return entry


async def verify_ledger(session: AsyncSession) -> dict[str, Any]:
    """Verify integrity of the entire ledger hash chain from GENESIS to head."""
    stmt = select(Ledger).order_by(Ledger.id.asc())
    res = await session.execute(stmt)
    rows = list(res.scalars().all())

    prev = "GENESIS"
    for i, r in enumerate(rows):
        body = {
            "id": r.id,
            "at": r.at.isoformat() if hasattr(r.at, "isoformat") else str(r.at),
            "actor": r.actor,
            "action": r.action,
            "payload": r.payload,
        }
        expected_hash = ledger_hash(prev, body)
        if r.prev_hash != prev or r.hash != expected_hash:
            return {"ok": False, "broken_at_index": i, "id": r.id}
        prev = r.hash

    return {"ok": True, "length": len(rows), "head": prev}


# =============================================================================
# 10. Quarantine Operations
# =============================================================================

async def insert_quarantine(
    session: AsyncSession,
    resource: str | None,
    errors: dict[str, Any] | list[Any] | None = None,
    raw_body: str | None = None,
    sim_tick: int | None = None,
    at: datetime | None = None,
) -> Quarantine:
    """Store malformed or rejected simulator responses in quarantine."""
    entry = Quarantine(
        resource=resource,
        errors=errors,
        raw_body=raw_body,
        sim_tick=sim_tick,
        at=at or datetime.now(UTC),
    )
    session.add(entry)
    await session.flush()
    return entry


# =============================================================================
# 11. Allocation Reconciliation Operations
# =============================================================================

async def insert_reconciliation(
    session: AsyncSession,
    decision_line_id: int,
    sent: float | None = None,
    received: float | None = None,
    loss: float | None = None,
    loss_reason: str | None = None,
    method: str | None = None,
) -> AllocationReconciliation:
    """Record reconciliation data for a shipment allocation line."""
    stmt = (
        select(AllocationReconciliation)
        .where(AllocationReconciliation.decision_line_id == decision_line_id)
        .with_for_update()
    )
    res = await session.execute(stmt)
    rec = res.scalar_one_or_none()

    if rec:
        if sent is not None:
            rec.sent = sent
        if received is not None:
            rec.received = received
        if loss is not None:
            rec.loss = loss
        if loss_reason is not None:
            rec.loss_reason = loss_reason
        if method is not None:
            rec.method = method
    else:
        rec = AllocationReconciliation(
            decision_line_id=decision_line_id,
            sent=sent,
            received=received,
            loss=loss,
            loss_reason=loss_reason,
            method=method,
        )
        session.add(rec)

    await session.flush()
    return rec


# =============================================================================
# 12. Decision Outcomes Operations
# =============================================================================

async def insert_outcome(
    session: AsyncSession,
    decision_id: int,
    evaluated_tick: int | None = None,
    predicted: dict[str, Any] | None = None,
    realised: dict[str, Any] | None = None,
    hit: bool | None = None,
) -> DecisionOutcome:
    """Record counterfactual or evaluated outcome for a planning decision."""
    stmt = (
        select(DecisionOutcome)
        .where(DecisionOutcome.decision_id == decision_id)
        .with_for_update()
    )
    res = await session.execute(stmt)
    outcome = res.scalar_one_or_none()

    if outcome:
        if evaluated_tick is not None:
            outcome.evaluated_tick = evaluated_tick
        if predicted is not None:
            outcome.predicted = predicted
        if realised is not None:
            outcome.realised = realised
        if hit is not None:
            outcome.hit = hit
    else:
        outcome = DecisionOutcome(
            decision_id=decision_id,
            evaluated_tick=evaluated_tick,
            predicted=predicted,
            realised=realised,
            hit=hit,
        )
        session.add(outcome)

    await session.flush()
    return outcome
