"""SQLAlchemy 2 async database models for BUP Fuel Supply Resilience Platform (FSIP).

Follows 04_DATA_MODEL_AND_API.md §1 and 13_ARCHITECTURE_V2.md §6.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy 2 declarative models."""
    pass


# =============================================================================
# 1. Simulator Lifetime & Snapshot Tables
# =============================================================================

class SimRun(Base):
    """One row per simulator lifetime (new row created after each detected reset)."""
    __tablename__ = "sim_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scenario_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    seed: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tick_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Relationships
    snapshots: Mapped[list[WorldSnapshot]] = relationship("WorldSnapshot", back_populates="sim_run")
    demand_observations: Mapped[list[DemandObservation]] = relationship("DemandObservation", back_populates="sim_run")
    forecasts: Mapped[list[Forecast]] = relationship("Forecast", back_populates="sim_run")
    risk_assessments: Mapped[list[RiskAssessment]] = relationship("RiskAssessment", back_populates="sim_run")
    decisions: Mapped[list[Decision]] = relationship("Decision", back_populates="sim_run")
    alerts: Mapped[list[Alert]] = relationship("Alert", back_populates="sim_run")


class WorldSnapshot(Base):
    """Compact world snapshot per processed tick (TimescaleDB hypertable partitioned by sim_time)."""
    __tablename__ = "world_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True)
    sim_run_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sim_runs.id"), nullable=True)
    tick: Mapped[int] = mapped_column(Integer, nullable=False)
    sim_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    depots: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    stations: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    routes: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    events: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    supply: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    stale: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    degraded: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("id", "sim_time"),
        UniqueConstraint("sim_run_id", "tick", "sim_time", name="uq_world_snapshots_sim_run_tick_time"),
        Index("ix_world_snapshots_sim_run_tick", "sim_run_id", "tick"),
    )

    sim_run: Mapped[SimRun | None] = relationship("SimRun", back_populates="snapshots")


class DemandObservation(Base):
    """Demand observations from /v1/demand-history (TimescaleDB hypertable partitioned by sim_time)."""
    __tablename__ = "demand_observations"

    sim_run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sim_runs.id"), nullable=False)
    sim_row_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    station_id: Mapped[str] = mapped_column(Text, nullable=False)
    fuel_type: Mapped[str] = mapped_column(Text, nullable=False)
    tick: Mapped[int] = mapped_column(Integer, nullable=False)
    sim_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    demand_liters: Mapped[float | None] = mapped_column(Float, nullable=True)
    served_liters: Mapped[float | None] = mapped_column(Float, nullable=True)
    unmet_liters: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("sim_run_id", "sim_row_id", "sim_time"),
        Index("ix_demand_observations_query", "sim_run_id", "station_id", "fuel_type", "tick"),
    )

    sim_run: Mapped[SimRun] = relationship("SimRun", back_populates="demand_observations")


# =============================================================================
# 2. Forecasting & Risk Assessment Tables
# =============================================================================

class Forecast(Base):
    """Forecasts used for each decision (for prediction-error and calibration metrics)."""
    __tablename__ = "forecasts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sim_run_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sim_runs.id"), nullable=True)
    made_at_tick: Mapped[int] = mapped_column(Integer, nullable=False)
    station_id: Mapped[str] = mapped_column(Text, nullable=False)
    fuel_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_tick: Mapped[int] = mapped_column(Integer, nullable=False)
    p10: Mapped[float] = mapped_column(Float, nullable=False)
    p50: Mapped[float] = mapped_column(Float, nullable=False)
    p90: Mapped[float] = mapped_column(Float, nullable=False)
    model_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_fallback: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)

    __table_args__ = (
        Index("ix_forecasts_lookup", "sim_run_id", "station_id", "fuel_type", "target_tick"),
    )

    sim_run: Mapped[SimRun | None] = relationship("SimRun", back_populates="forecasts")


class RiskAssessment(Base):
    """Risk assessment per (station, fuel) per decision cycle."""
    __tablename__ = "risk_assessments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sim_run_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sim_runs.id"), nullable=True)
    tick: Mapped[int] = mapped_column(Integer, nullable=False)
    station_id: Mapped[str] = mapped_column(Text, nullable=False)
    fuel_type: Mapped[str] = mapped_column(Text, nullable=False)
    inventory: Mapped[float | None] = mapped_column(Float, nullable=True)
    inflight: Mapped[float | None] = mapped_column(Float, nullable=True)
    hours_to_stockout: Mapped[float | None] = mapped_column(Float, nullable=True)
    stockout_prob: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_level: Mapped[str] = mapped_column(Text, nullable=False)  # LOW|MEDIUM|HIGH|CRITICAL
    drivers: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        Index("ix_risk_assessments_tick", "sim_run_id", "tick", "station_id", "fuel_type"),
    )

    sim_run: Mapped[SimRun | None] = relationship("SimRun", back_populates="risk_assessments")


# =============================================================================
# 3. Decision & Execution Tables
# =============================================================================

class Decision(Base):
    """Planner output: one decision per planning cycle."""
    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sim_run_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sim_runs.id"), nullable=True)
    tick: Mapped[int] = mapped_column(Integer, nullable=False)
    policy: Mapped[str | None] = mapped_column(Text, nullable=True)
    policy_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    degraded: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    fallback_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    objective: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    impact: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    explanation: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    narrative: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    sim_run: Mapped[SimRun | None] = relationship("SimRun", back_populates="decisions")
    lines: Mapped[list[DecisionLine]] = relationship(
        "DecisionLine", back_populates="decision", cascade="all, delete-orphan"
    )


class DecisionLine(Base):
    """Individual shipment allocation within a planning decision."""
    __tablename__ = "decision_lines"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    decision_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("decisions.id"), nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    source_depot_id: Mapped[str] = mapped_column(Text, nullable=False)
    destination_station_id: Mapped[str] = mapped_column(Text, nullable=False)
    route_id: Mapped[str] = mapped_column(Text, nullable=False)
    fuel_type: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    sim_allocation_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sim_error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_arrival_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_arrival_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    received_liters: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    decision: Mapped[Decision] = relationship("Decision", back_populates="lines")


# =============================================================================
# 4. Alerts, Incidents & Operations Audit
# =============================================================================

class Alert(Base):
    """System and operational alerts with open-alert deduplication index."""
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sim_run_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sim_runs.id"), nullable=True)
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)  # INFO|WARNING|CRITICAL
    title: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    entity_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    entity_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    fuel_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)  # OPEN|ACKNOWLEDGED|RESOLVED
    opened_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolved_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    related_decision_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (
        Index("alerts_open_fp", "fingerprint", unique=True, postgresql_where=text("status <> 'RESOLVED'")),
    )

    sim_run: Mapped[SimRun | None] = relationship("SimRun", back_populates="alerts")


class Incident(Base):
    """Platform infrastructure incidents with timeline tracking."""
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    component: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)  # OPEN|MITIGATED|RESOLVED
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mitigated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    timeline: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)


class AuditLog(Base):
    """Operator action audit log."""
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    actor: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str | None] = mapped_column(Text, nullable=True)
    entity_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    entity_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


# =============================================================================
# 5. Model Registry & Evaluations
# =============================================================================

class ModelVersion(Base):
    """Shared machine learning model version registry."""
    __tablename__ = "model_versions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    params: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    artifact_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)

    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_model_versions_name_version"),
    )


class EvalRun(Base):
    """Policy evaluation benchmark runs."""
    __tablename__ = "eval_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scenario: Mapped[str | None] = mapped_column(Text, nullable=True)
    policy: Mapped[str | None] = mapped_column(Text, nullable=True)
    ticks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    seed: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    service_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    unmet_liters: Mapped[float | None] = mapped_column(Float, nullable=True)
    failures: Mapped[int | None] = mapped_column(Integer, nullable=True)
    overflow_liters: Mapped[float | None] = mapped_column(Float, nullable=True)
    liters_shipped: Mapped[float | None] = mapped_column(Float, nullable=True)
    cross_region_liters: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_station_service: Mapped[float | None] = mapped_column(Float, nullable=True)
    runtime_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# =============================================================================
# 6. V2 Additional Tables (Tamper-evident ledger, flags, quarantine, etc.)
# =============================================================================

class Ledger(Base):
    """Append-only, tamper-evident hash-chained audit ledger."""
    __tablename__ = "ledger"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    prev_hash: Mapped[str] = mapped_column(Text, nullable=False)
    hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)


class FeatureFlag(Base):
    """Runtime toggles and configurations without redeployment."""
    __tablename__ = "feature_flags"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[dict[str, Any] | Any] = mapped_column(JSONB, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Quarantine(Base):
    """Quarantine store for rejected/malformed simulator responses."""
    __tablename__ = "quarantine"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    resource: Mapped[str | None] = mapped_column(Text, nullable=True)
    errors: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONB, nullable=True)
    raw_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    sim_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AllocationReconciliation(Base):
    """Reconciliation of planned vs realized shipments (spillage and losses)."""
    __tablename__ = "allocation_reconciliation"

    decision_line_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("decision_lines.id"), primary_key=True)
    sent: Mapped[float | None] = mapped_column(Float, nullable=True)
    received: Mapped[float | None] = mapped_column(Float, nullable=True)
    loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    loss_reason: Mapped[str | None] = mapped_column(Text, nullable=True)  # OVERFLOW|FAILED_ROUTE|NONE
    method: Mapped[str | None] = mapped_column(Text, nullable=True)       # audit|estimate

    decision_line: Mapped[DecisionLine] = relationship("DecisionLine")


class DecisionOutcome(Base):
    """Predicted vs actual outcomes of planning decisions."""
    __tablename__ = "decision_outcomes"

    decision_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("decisions.id"), primary_key=True)
    evaluated_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    predicted: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    realised: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    hit: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    decision: Mapped[Decision] = relationship("Decision")


class PlaybookRun(Base):
    """Execution history for automated crisis playbooks."""
    __tablename__ = "playbook_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    playbook_id: Mapped[str] = mapped_column(Text, nullable=False)
    sim_run_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sim_runs.id"), nullable=True)
    started_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)  # RUNNING|COMPLETED|ABORTED
    steps: Mapped[list[Any] | dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sim_run: Mapped[SimRun | None] = relationship("SimRun")
