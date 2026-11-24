"""Unit tests for LightGBM quantile regression model.

Tests specifications in PHASE_05_FORECASTING.md Task 5.3 and 05_INTELLIGENCE_DESIGN.md §1.3.
"""

from pathlib import Path

import numpy as np
import pandas as pd
from app.models.gbm import ANCHOR_HORIZONS, GBMQuantileModel


def _generate_synthetic_dataframe(n_ticks: int = 192) -> pd.DataFrame:
    """Generates synthetic demand rows for testing GBM training and feature pipeline."""
    rows = []
    stations = ["station-mirpur", "station-tongi"]
    fuels = ["DIESEL", "PETROL"]
    profiles = {"station-mirpur": "urban_high", "station-tongi": "industrial"}
    regions = {"station-mirpur": ("dhaka", 1.00), "station-tongi": ("dhaka", 1.00)}

    for run_idx in [1, 2]:
        for sid in stations:
            prof = profiles[sid]
            reg, reg_f = regions[sid]
            for f in fuels:
                for t in range(n_ticks):
                    hour = (t * 15 // 60) % 24
                    mult = 1.8 if (t % 50 < 10) else 1.0
                    base_d = 80.0 if f == "DIESEL" else 50.0
                    demand = base_d * mult * (1.0 + 0.05 * np.sin(hour))
                    rows.append(
                        {
                            "run": run_idx,
                            "tick": t,
                            "sim_time": f"2026-01-01T{hour:02d}:00:00Z",
                            "hour": hour,
                            "station_id": sid,
                            "fuel_type": f,
                            "profile": prof,
                            "region_id": reg,
                            "region_factor": reg_f,
                            "demand_multiplier": mult,
                            "demand_liters": demand,
                            "served_liters": demand,
                            "unmet_liters": 0.0,
                            "event_spike_active": mult > 1.0,
                        }
                    )
    return pd.DataFrame(rows)


def test_gbm_fit_and_predict_contract() -> None:
    """GBM fits on data and returns valid quantile forecasts with p10 <= p50 <= p90."""
    df = _generate_synthetic_dataframe(n_ticks=150)
    model = GBMQuantileModel()
    model.fit(df, n_estimators=10, num_leaves=15)

    assert "p10" in model.models
    assert "p50" in model.models
    assert "p90" in model.models

    # Predict for horizon 48
    history_demand = [80.0] * 96
    history_ticks = list(range(96))
    history_mults = [1.0] * 96

    pred = model.predict_series(
        station_id="station-mirpur",
        fuel_type="DIESEL",
        history_ticks=history_ticks,
        history_demand=history_demand,
        history_multipliers=history_mults,
        start_tick=95,
        horizon=48,
        multiplier_now=1.0,
    )

    assert len(pred["tick"]) == 48
    assert len(pred["p10"]) == 48
    assert len(pred["p50"]) == 48
    assert len(pred["p90"]) == 48

    for p10, p50, p90 in zip(pred["p10"], pred["p50"], pred["p90"], strict=True):
        assert np.isfinite(p10) and p10 >= 0.0
        assert np.isfinite(p50) and p50 >= 0.0
        assert np.isfinite(p90) and p90 >= 0.0
        assert p10 <= p50 <= p90


def test_gbm_save_load_roundtrip(tmp_path: Path) -> None:
    """GBM model can be persisted and reloaded with identical predictions."""
    df = _generate_synthetic_dataframe(n_ticks=100)
    model = GBMQuantileModel(version="1.2.3")
    model.fit(df, n_estimators=5, num_leaves=7)

    save_path = tmp_path / "model.joblib"
    model.save(save_path)
    assert save_path.exists()

    loaded = GBMQuantileModel.load(save_path)
    assert loaded.version == "1.2.3"
    assert loaded.anchor_horizons == ANCHOR_HORIZONS

    history_demand = [60.0] * 40
    history_ticks = list(range(40))
    history_mults = [1.0] * 40

    pred_orig = model.predict_series(
        station_id="station-tongi",
        fuel_type="PETROL",
        history_ticks=history_ticks,
        history_demand=history_demand,
        history_multipliers=history_mults,
        start_tick=39,
        horizon=24,
        multiplier_now=1.2,
    )
    pred_loaded = loaded.predict_series(
        station_id="station-tongi",
        fuel_type="PETROL",
        history_ticks=history_ticks,
        history_demand=history_demand,
        history_multipliers=history_mults,
        start_tick=39,
        horizon=24,
        multiplier_now=1.2,
    )

    assert pred_orig["p50"] == pred_loaded["p50"]
    assert pred_orig["p10"] == pred_loaded["p10"]
    assert pred_orig["p90"] == pred_loaded["p90"]
