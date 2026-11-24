"""Unit tests for training and backtest pipeline.

Tests specifications in PHASE_05_FORECASTING.md Task 5.4.
"""

from pathlib import Path

import pandas as pd
from training.train import TrainingPipeline


def test_training_pipeline_execution(tmp_path: Path) -> None:
    """Pipeline executes end-to-end on sample data, persists artifacts, and writes evidence."""
    # Create mini dataset
    rows = []
    stations = ["station-mirpur", "station-tongi"]
    fuels = ["DIESEL", "PETROL"]

    for run_id in [1, 2]:
        for sid in stations:
            for f in fuels:
                for t in range(120):
                    hour = (t * 15 // 60) % 24
                    mult = 1.0
                    d = 70.0 + 10.0 * (hour % 3)
                    rows.append(
                        {
                            "run": run_id,
                            "tick": t,
                            "sim_time": f"2026-01-01T{hour:02d}:00:00Z",
                            "hour": hour,
                            "station_id": sid,
                            "fuel_type": f,
                            "profile": "urban_high",
                            "region_id": "dhaka",
                            "region_factor": 1.00,
                            "demand_multiplier": mult,
                            "demand_liters": d,
                            "served_liters": d,
                            "unmet_liters": 0.0,
                            "event_spike_active": False,
                        }
                    )

    data_file = tmp_path / "mini_demand.parquet"
    pd.DataFrame(rows).to_parquet(data_file)

    artifacts_dir = tmp_path / "artifacts"
    evidence_file = tmp_path / "forecast-backtest.md"

    pipeline = TrainingPipeline(
        data_path=data_file,
        artifacts_dir=artifacts_dir,
        evidence_path=evidence_file,
        test_fraction=0.20,
        eval_horizons=[1, 4, 12],
    )

    result = pipeline.run(n_estimators=5)

    assert result["chosen_model"] in ("seasonal", "lightgbm")
    assert (artifacts_dir / "active.json").exists()
    assert (artifacts_dir / "experiments.jsonl").exists()
    assert (artifacts_dir / "seasonal" / "builtin" / "model.joblib").exists()
    assert (artifacts_dir / "lightgbm" / "1.0.0" / "model.joblib").exists()
    assert evidence_file.exists()
