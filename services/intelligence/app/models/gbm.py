"""LightGBM multi-horizon quantile regression forecasting model.

Implements GBMQuantileModel for p10, p50, and p90 demand forecasts.
Specifications:
- 05_INTELLIGENCE_DESIGN.md §1.3
- PHASE_05_FORECASTING.md Task 5.3
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd

ANCHOR_HORIZONS: list[int] = [1, 2, 4, 8, 12, 24, 48]

STATION_MAP: dict[str, int] = {
    "station-mirpur": 0,
    "station-tongi": 1,
    "station-karnaphuli": 2,
    "station-coxsbazar": 3,
}

FUEL_MAP: dict[str, int] = {
    "DIESEL": 0,
    "PETROL": 1,
    "OCTANE": 2,
}

PROFILE_MAP: dict[str, int] = {
    "urban_high": 0,
    "industrial": 1,
    "highway": 2,
    "regional": 3,
}

STATION_METADATA: dict[str, dict[str, Any]] = {
    "station-mirpur": {"profile": "urban_high", "region_id": "dhaka", "region_factor": 1.00},
    "station-tongi": {"profile": "industrial", "region_id": "dhaka", "region_factor": 1.00},
    "station-karnaphuli": {"profile": "highway", "region_id": "chattogram", "region_factor": 1.08},
    "station-coxsbazar": {"profile": "regional", "region_id": "chattogram", "region_factor": 1.08},
}

FEATURE_COLUMNS: list[str] = [
    "hour",
    "dow",
    "minute_of_day",
    "station_code",
    "fuel_code",
    "profile_code",
    "region_factor",
    "demand_multiplier",
    "lag_1",
    "lag_2",
    "lag_4",
    "lag_96",
    "rolling_mean_8",
    "rolling_std_8",
    "horizon_k",
    "event_spike_active",
]


@dataclass
class GBMQuantileModel:
    """LightGBM quantile regression model predicting p10, p50, and p90 demand."""

    name: str = "lightgbm"
    version: str = "1.0.0"
    models: dict[str, lgb.LGBMRegressor] = field(default_factory=dict)
    feature_names: list[str] = field(default_factory=lambda: list(FEATURE_COLUMNS))
    metrics: dict[str, float] = field(default_factory=dict)
    anchor_horizons: list[int] = field(default_factory=lambda: list(ANCHOR_HORIZONS))

    def fit(
        self,
        df: pd.DataFrame,
        n_estimators: int = 120,
        learning_rate: float = 0.05,
        num_leaves: int = 31,
        random_state: int = 42,
    ) -> "GBMQuantileModel":
        """Prepares multi-horizon training features and fits alpha=0.1, 0.5, 0.9 regressors."""
        train_df = self._prepare_training_features(df)
        if train_df.empty:
            raise ValueError("Training dataset produced 0 valid feature rows.")

        x_train = train_df[self.feature_names]
        y_train = train_df["target_demand"]

        quantiles = {"p10": 0.1, "p50": 0.5, "p90": 0.9}
        fitted_models: dict[str, lgb.LGBMRegressor] = {}

        for q_name, alpha in quantiles.items():
            model = lgb.LGBMRegressor(
                objective="quantile",
                alpha=alpha,
                n_estimators=n_estimators,
                learning_rate=learning_rate,
                num_leaves=num_leaves,
                random_state=random_state,
                n_jobs=-1,
                verbosity=-1,
            )
            model.fit(x_train, y_train)
            fitted_models[q_name] = model

        self.models = fitted_models
        return self

    def _prepare_training_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Expands dataset for anchor horizons k in {1, 2, 4, 8, 12, 24, 48}."""
        sorted_df = df.sort_values(by=["run", "station_id", "fuel_type", "tick"]).copy()

        # Compute series-level lag and rolling features
        grouped = sorted_df.groupby(["run", "station_id", "fuel_type"])
        sorted_df["lag_1"] = grouped["demand_liters"].shift(0)
        sorted_df["lag_2"] = grouped["demand_liters"].shift(1)
        sorted_df["lag_4"] = grouped["demand_liters"].shift(3)
        sorted_df["lag_96"] = grouped["demand_liters"].shift(95)
        sorted_df["rolling_mean_8"] = grouped["demand_liters"].transform(
            lambda s: s.rolling(window=8, min_periods=1).mean()
        )
        sorted_df["rolling_std_8"] = (
            grouped["demand_liters"]
            .transform(lambda s: s.rolling(window=8, min_periods=1).std())
            .fillna(0.0)
        )

        # Fill lag_96 with rolling mean if prior day observations not available
        sorted_df["lag_96"] = sorted_df["lag_96"].fillna(sorted_df["rolling_mean_8"])
        sorted_df["lag_4"] = sorted_df["lag_4"].fillna(sorted_df["rolling_mean_8"])
        sorted_df["lag_2"] = sorted_df["lag_2"].fillna(sorted_df["lag_1"])

        # Map categorical codes
        sorted_df["station_code"] = sorted_df["station_id"].map(
            lambda sid: STATION_MAP.get(str(sid), 0)
        )
        sorted_df["fuel_code"] = sorted_df["fuel_type"].map(
            lambda f: FUEL_MAP.get(str(f), 0)
        )
        sorted_df["profile_code"] = sorted_df["profile"].map(
            lambda p: PROFILE_MAP.get(str(p), 0)
        )

        anchor_frames: list[pd.DataFrame] = []
        for k in self.anchor_horizons:
            k_df = sorted_df.copy()
            k_df["horizon_k"] = k
            k_df["target_demand"] = grouped["demand_liters"].shift(-k)
            # Recompute target-tick features for the target tick = tick + k
            target_tick = k_df["tick"] + k
            k_df["hour"] = (target_tick * 15 // 60) % 24
            k_df["dow"] = (target_tick // 96) % 7
            k_df["minute_of_day"] = (target_tick * 15) % 1440
            # Target tick demand multiplier
            k_df["demand_multiplier"] = grouped["demand_multiplier"].shift(-k).fillna(k_df["demand_multiplier"])
            k_df["event_spike_active"] = (k_df["demand_multiplier"] > 1.0).astype(int)

            k_valid = k_df.dropna(subset=["target_demand"]).copy()
            anchor_frames.append(k_valid)

        if not anchor_frames:
            return pd.DataFrame()

        return pd.concat(anchor_frames, ignore_index=True)

    def predict_series(
        self,
        station_id: str,
        fuel_type: str,
        history_ticks: list[int],
        history_demand: list[float],
        history_multipliers: list[float],
        start_tick: int,
        horizon: int,
        multiplier_now: float,
        multiplier_schedule: list[tuple[int, int, float]] = (),
    ) -> dict[str, list[float]]:
        """Generates p10, p50, and p90 predictions for horizon ticks [start_tick + 1 .. start_tick + horizon].

        Predicts anchor horizons and linearly interpolates intermediate horizons.
        Guarantees non-negative, finite values and p10 <= p50 <= p90.
        """
        if not self.models:
            raise RuntimeError("GBMQuantileModel has not been fitted or loaded.")

        # Compute history summary features
        if history_demand:
            lag_1 = float(history_demand[-1])
            lag_2 = float(history_demand[-2]) if len(history_demand) >= 2 else lag_1
            lag_4 = float(history_demand[-4]) if len(history_demand) >= 4 else lag_1
            lag_96 = float(history_demand[-96]) if len(history_demand) >= 96 else float(np.mean(history_demand))
            rolling_window = history_demand[-8:]
            rolling_mean_8 = float(np.mean(rolling_window))
            rolling_std_8 = float(np.std(rolling_window)) if len(rolling_window) > 1 else 0.0
        else:
            lag_1 = 0.0
            lag_2 = 0.0
            lag_4 = 0.0
            lag_96 = 0.0
            rolling_mean_8 = 0.0
            rolling_std_8 = 0.0

        meta = STATION_METADATA.get(
            station_id,
            {"profile": "urban_high", "region_id": "dhaka", "region_factor": 1.00},
        )
        station_code = STATION_MAP.get(station_id, 0)
        fuel_code = FUEL_MAP.get(fuel_type, 0)
        profile_code = PROFILE_MAP.get(meta["profile"], 0)
        region_factor = float(meta["region_factor"])

        sched_bounds = [(from_t, to_t, mult) for from_t, to_t, mult in multiplier_schedule]
        max_sched_to = max((to_t for _, to_t, _ in sched_bounds), default=-1)

        # Predict for anchor horizons
        anchor_rows: list[dict[str, Any]] = []
        for k in self.anchor_horizons:
            target_t = start_tick + k
            hour = (target_t * 15 // 60) % 24
            dow = (target_t // 96) % 7
            minute_of_day = (target_t * 15) % 1440

            # Determine multiplier at target_t
            mult_t: float | None = None
            for from_t, to_t, s_mult in sched_bounds:
                if from_t <= target_t < to_t:
                    mult_t = s_mult
                    break
            if mult_t is None:
                if sched_bounds:
                    mult_t = 1.0 if target_t >= max_sched_to else multiplier_now
                else:
                    mult_t = multiplier_now

            anchor_rows.append(
                {
                    "hour": hour,
                    "dow": dow,
                    "minute_of_day": minute_of_day,
                    "station_code": station_code,
                    "fuel_code": fuel_code,
                    "profile_code": profile_code,
                    "region_factor": region_factor,
                    "demand_multiplier": mult_t,
                    "lag_1": lag_1,
                    "lag_2": lag_2,
                    "lag_4": lag_4,
                    "lag_96": lag_96,
                    "rolling_mean_8": rolling_mean_8,
                    "rolling_std_8": rolling_std_8,
                    "horizon_k": k,
                    "event_spike_active": int(mult_t > 1.0),
                }
            )

        x_anchors = pd.DataFrame(anchor_rows)[self.feature_names]
        anchors_p10 = np.maximum(0.0, self.models["p10"].predict(x_anchors))
        anchors_p50 = np.maximum(0.0, self.models["p50"].predict(x_anchors))
        anchors_p90 = np.maximum(0.0, self.models["p90"].predict(x_anchors))

        # Enforce anchor quantile monotonicity
        anchors_p10 = np.minimum(anchors_p10, anchors_p50)
        anchors_p90 = np.maximum(anchors_p90, anchors_p50)

        # Interpolate for all target horizons [1 .. horizon]
        target_ks = np.arange(1, horizon + 1)
        pred_p10 = np.interp(target_ks, self.anchor_horizons, anchors_p10)
        pred_p50 = np.interp(target_ks, self.anchor_horizons, anchors_p50)
        pred_p90 = np.interp(target_ks, self.anchor_horizons, anchors_p90)

        # Enforce non-negative, finite, and p10 <= p50 <= p90
        pred_p10 = np.maximum(0.0, pred_p10)
        pred_p50 = np.maximum(0.0, pred_p50)
        pred_p90 = np.maximum(pred_p50, pred_p90)
        pred_p10 = np.minimum(pred_p10, pred_p50)

        ticks_out = [float(start_tick + k) for k in target_ks]
        return {
            "tick": ticks_out,
            "p10": [round(float(v), 2) for v in pred_p10],
            "p50": [round(float(v), 2) for v in pred_p50],
            "p90": [round(float(v), 2) for v in pred_p90],
        }

    def save(self, filepath: Path | str) -> None:
        """Saves model bundle to disk via joblib."""
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, path)

    @classmethod
    def load(cls, filepath: Path | str) -> "GBMQuantileModel":
        """Loads model bundle from disk via joblib."""
        loaded = joblib.load(filepath)
        if not isinstance(loaded, cls):
            raise TypeError(f"Loaded object is not an instance of {cls.__name__}")
        return loaded
