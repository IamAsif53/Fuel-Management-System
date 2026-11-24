"""Unit tests for Phase 05 Task 5.1: Dataset validation."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

DATASET_PATH = Path(__file__).resolve().parent.parent / "data" / "demand.parquet"


@pytest.mark.skipif(not DATASET_PATH.exists(), reason="demand.parquet not generated yet")
def test_dataset_schema_and_types():
    df = pd.read_parquet(DATASET_PATH)
    assert len(df) == 34560, f"Expected 34,560 rows, got {len(df)}"

    expected_cols = [
        "run",
        "tick",
        "sim_time",
        "hour",
        "station_id",
        "fuel_type",
        "profile",
        "region_id",
        "region_factor",
        "demand_multiplier",
        "demand_liters",
        "served_liters",
        "unmet_liters",
        "event_spike_active",
    ]
    for col in expected_cols:
        assert col in df.columns, f"Missing required column {col}"

    assert (df["demand_liters"] >= 0).all(), "Negative demand_liters found"
    assert (df["served_liters"] >= 0).all(), "Negative served_liters found"
    assert (df["unmet_liters"] >= 0).all(), "Negative unmet_liters found"
    assert df["run"].nunique() == 6
    assert df["station_id"].nunique() == 4
    assert df["fuel_type"].nunique() == 3

    # Verify hour derivation
    expected_hours = (df["tick"] * 15 // 60) % 24
    assert (df["hour"] == expected_hours).all(), "Hour column mismatch"

    # Verify spike active flag
    expected_spikes = df["demand_multiplier"] > 1.001
    assert (df["event_spike_active"] == expected_spikes).all(), "event_spike_active flag mismatch"
