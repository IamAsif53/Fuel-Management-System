"""Unit tests for SQLAlchemy 2 async database models (Task 2.1).

Validates all 18 tables, column names, types, nullabilities, primary keys, foreign keys,
unique constraints, indexes, and relationships against the Phase 02 locked specification.
"""
from __future__ import annotations

from app.db.models import (
    Base,
)
from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB

EXPECTED_TABLES = {
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
}


def test_all_expected_tables_declared_in_metadata():
    """Verify that all 18 required tables are registered in Base.metadata."""
    table_names = set(Base.metadata.tables.keys())
    assert table_names == EXPECTED_TABLES


def test_sim_runs_table_schema():
    table = Base.metadata.tables["sim_runs"]
    pk_cols = [c.name for c in table.primary_key.columns]
    assert pk_cols == ["id"]
    expected_cols = {"id", "scenario_id", "seed", "tick_minutes", "started_at", "ended_at", "first_tick", "last_tick"}
    assert set(table.c.keys()) == expected_cols
    assert "status" not in table.c
    assert isinstance(table.c.id.type, BigInteger)
    assert isinstance(table.c.scenario_id.type, Text | String)
    assert isinstance(table.c.seed.type, BigInteger)
    assert isinstance(table.c.tick_minutes.type, Integer)
    assert isinstance(table.c.started_at.type, DateTime)
    assert table.c.started_at.type.timezone is True
    assert isinstance(table.c.ended_at.type, DateTime)
    assert table.c.ended_at.type.timezone is True
    assert isinstance(table.c.first_tick.type, Integer)
    assert isinstance(table.c.last_tick.type, Integer)


def test_world_snapshots_hypertable_composite_pk_and_uniques():
    """Verify world_snapshots has composite PK including sim_time for TimescaleDB hypertable."""
    table = Base.metadata.tables["world_snapshots"]
    pk_cols = [c.name for c in table.primary_key.columns]
    assert "id" in pk_cols and "sim_time" in pk_cols
    assert table.c.sim_time.type.timezone is True

    # Check unique constraint on (sim_run_id, tick, sim_time)
    uq_names = [uq.name for uq in table.constraints if uq.name == "uq_world_snapshots_sim_run_tick_time"]
    assert len(uq_names) == 1

    # Check JSONB columns
    assert isinstance(table.c.depots.type, JSONB)
    assert isinstance(table.c.stations.type, JSONB)
    assert isinstance(table.c.routes.type, JSONB)
    assert isinstance(table.c.events.type, JSONB)
    assert isinstance(table.c.supply.type, JSONB)
    assert isinstance(table.c.metrics.type, JSONB)

    # Check boolean flags with defaults
    assert isinstance(table.c.stale.type, Boolean)
    assert isinstance(table.c.degraded.type, Boolean)


def test_demand_observations_hypertable_pk_and_indexes():
    """Verify demand_observations has composite PK (sim_run_id, sim_row_id, sim_time)."""
    table = Base.metadata.tables["demand_observations"]
    pk_cols = [c.name for c in table.primary_key.columns]
    assert pk_cols == ["sim_run_id", "sim_row_id", "sim_time"]
    assert table.c.sim_time.type.timezone is True

    # Index on (sim_run_id, station_id, fuel_type, tick)
    idx_cols = []
    for idx in table.indexes:
        if idx.name == "ix_demand_observations_query":
            idx_cols = [c.name for c in idx.columns]
    assert idx_cols == ["sim_run_id", "station_id", "fuel_type", "tick"]


def test_forecasts_table_schema_and_time_columns():
    table = Base.metadata.tables["forecasts"]
    assert [c.name for c in table.primary_key.columns] == ["id"]
    assert isinstance(table.c.p10.type, Float)
    assert isinstance(table.c.p50.type, Float)
    assert isinstance(table.c.p90.type, Float)
    assert isinstance(table.c.is_fallback.type, Boolean)

    # V2 time column and created_at
    assert "sim_time" in table.c
    assert table.c.sim_time.type.timezone is True
    assert table.c.sim_time.nullable is False
    assert "created_at" in table.c
    assert table.c.created_at.type.timezone is True

    idx_cols = []
    for idx in table.indexes:
        if idx.name == "ix_forecasts_lookup":
            idx_cols = [c.name for c in idx.columns]
    assert idx_cols == ["sim_run_id", "station_id", "fuel_type", "target_tick"]


def test_risk_assessments_table_schema_and_time_columns():
    table = Base.metadata.tables["risk_assessments"]
    assert [c.name for c in table.primary_key.columns] == ["id"]
    assert isinstance(table.c.drivers.type, JSONB)

    # V2 time column and created_at
    assert "sim_time" in table.c
    assert table.c.sim_time.type.timezone is True
    assert table.c.sim_time.nullable is False
    assert "created_at" in table.c
    assert table.c.created_at.type.timezone is True

    idx_cols = []
    for idx in table.indexes:
        if idx.name == "ix_risk_assessments_tick":
            idx_cols = [c.name for c in idx.columns]
    assert idx_cols == ["sim_run_id", "tick", "station_id", "fuel_type"]


def test_decisions_and_decision_lines_contract():
    dec_table = Base.metadata.tables["decisions"]
    line_table = Base.metadata.tables["decision_lines"]

    assert [c.name for c in dec_table.primary_key.columns] == ["id"]
    assert [c.name for c in line_table.primary_key.columns] == ["id"]

    # Verify decision_lines fields against 04 contract
    assert "destination_station_id" in line_table.c
    assert "quantity" in line_table.c
    assert "dest_station_id" not in line_table.c
    assert "liters" not in line_table.c
    assert "created_at" in line_table.c

    # Foreign key from decision_lines to decisions
    fks = list(line_table.c.decision_id.foreign_keys)
    assert len(fks) == 1
    assert fks[0].target_fullname == "decisions.id"

    # Idempotency key uniqueness
    assert line_table.c.idempotency_key.unique is True or any(
        uq.columns.contains(line_table.c.idempotency_key) for uq in line_table.constraints
    )


def test_alerts_partial_index():
    table = Base.metadata.tables["alerts"]
    alert_idx = next((idx for idx in table.indexes if idx.name == "alerts_open_fp"), None)
    assert alert_idx is not None
    assert alert_idx.unique is True
    assert "status <> 'RESOLVED'" in str(alert_idx.dialect_options.get("postgresql", {}).get("where", ""))


def test_audit_log_contract():
    table = Base.metadata.tables["audit_log"]
    assert [c.name for c in table.primary_key.columns] == ["id"]
    assert "entity_type" in table.c
    assert "entity_id" in table.c
    assert "payload" in table.c
    assert "resource_type" not in table.c
    assert "resource_id" not in table.c
    assert "diff_json" not in table.c


def test_ledger_append_only_contract():
    table = Base.metadata.tables["ledger"]
    assert [c.name for c in table.primary_key.columns] == ["id"]
    expected_cols = {"id", "at", "actor", "action", "payload", "prev_hash", "hash"}
    assert set(table.c.keys()) == expected_cols
    assert "entry_type" not in table.c
    assert "amount_bdt" not in table.c
    assert "sha256_hash" not in table.c
    assert table.c.at.type.timezone is True
    assert table.c.hash.unique is True or any(
        uq.columns.contains(table.c.hash) for uq in table.constraints
    )
    assert isinstance(table.c.payload.type, JSONB)


def test_eval_runs_contract():
    table = Base.metadata.tables["eval_runs"]
    assert [c.name for c in table.primary_key.columns] == ["id"]
    expected_cols = {
        "id", "scenario", "policy", "ticks", "seed", "service_level",
        "unmet_liters", "failures", "overflow_liters", "liters_shipped",
        "cross_region_liters", "min_station_service", "runtime_sec",
        "details", "created_at"
    }
    assert set(table.c.keys()) == expected_cols
    assert "wape" not in table.c
    assert "mape" not in table.c
    assert "rmse" not in table.c


def test_incidents_contract():
    table = Base.metadata.tables["incidents"]
    assert [c.name for c in table.primary_key.columns] == ["id"]
    expected_cols = {
        "id", "kind", "component", "status", "started_at",
        "mitigated_at", "resolved_at", "summary", "timeline", "created_at"
    }
    assert set(table.c.keys()) == expected_cols
    assert "incident_key" not in table.c
    assert "declared_tick" not in table.c


def test_model_versions_contract():
    table = Base.metadata.tables["model_versions"]
    assert [c.name for c in table.primary_key.columns] == ["id"]
    expected_cols = {"id", "name", "version", "created_at", "metrics", "params", "artifact_path", "is_active"}
    assert set(table.c.keys()) == expected_cols
    uq = next((c for c in table.constraints if c.name == "uq_model_versions_name_version"), None)
    assert uq is not None
    assert [c.name for c in uq.columns] == ["name", "version"]


def test_feature_flags_contract():
    table = Base.metadata.tables["feature_flags"]
    assert [c.name for c in table.primary_key.columns] == ["key"]
    expected_cols = {"key", "value", "updated_by", "updated_at"}
    assert set(table.c.keys()) == expected_cols
    assert isinstance(table.c.value.type, JSONB)


def test_quarantine_contract():
    table = Base.metadata.tables["quarantine"]
    assert [c.name for c in table.primary_key.columns] == ["id"]
    expected_cols = {"id", "at", "resource", "errors", "raw_body", "sim_tick"}
    assert set(table.c.keys()) == expected_cols
    assert "payload_type" not in table.c
    assert isinstance(table.c.errors.type, JSONB)
    assert isinstance(table.c.raw_body.type, Text | String)


def test_allocation_reconciliation_contract():
    table = Base.metadata.tables["allocation_reconciliation"]
    assert [c.name for c in table.primary_key.columns] == ["decision_line_id"]
    expected_cols = {"decision_line_id", "sent", "received", "loss", "loss_reason", "method", "created_at"}
    assert set(table.c.keys()) == expected_cols
    assert "dispatched_liters" not in table.c
    assert "delivered_liters" not in table.c
    assert "variance_liters" not in table.c
    assert "created_at" in table.c
    assert table.c.created_at.type.timezone is True
    assert table.c.created_at.nullable is False
    fks = list(table.c.decision_line_id.foreign_keys)
    assert fks[0].target_fullname == "decision_lines.id"


def test_decision_outcomes_contract():
    table = Base.metadata.tables["decision_outcomes"]
    assert [c.name for c in table.primary_key.columns] == ["decision_id"]
    expected_cols = {"decision_id", "evaluated_tick", "predicted", "realised", "hit", "created_at"}
    assert set(table.c.keys()) == expected_cols
    assert "actual_stockouts_avoided" not in table.c
    assert "counterfactual_penalty_bdt" not in table.c
    assert "created_at" in table.c
    assert table.c.created_at.type.timezone is True
    assert table.c.created_at.nullable is False
    fks = list(table.c.decision_id.foreign_keys)
    assert fks[0].target_fullname == "decisions.id"


def test_playbook_runs_contract():
    table = Base.metadata.tables["playbook_runs"]
    assert [c.name for c in table.primary_key.columns] == ["id"]
    expected_cols = {"id", "playbook_id", "sim_run_id", "started_tick", "status", "steps", "started_at", "ended_at"}
    assert set(table.c.keys()) == expected_cols
    assert isinstance(table.c.steps.type, JSONB)
    fks = list(table.c.sim_run_id.foreign_keys)
    assert fks[0].target_fullname == "sim_runs.id"
