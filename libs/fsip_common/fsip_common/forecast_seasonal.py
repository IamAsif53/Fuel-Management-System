"""Shared seasonal forecast model for FSIP.

Implements SeasonalProfileModel and SeriesHistory.
Used by both services/intelligence and services/engine as the zero-dependency fallback.
Specifications:
- 05_INTELLIGENCE_DESIGN.md §1.2
- 23_MODULE_SPECS.md §A2
"""

import statistics
from dataclasses import dataclass, field


@dataclass
class SeriesHistory:
    """Historical demand observations for a single (station, fuel) series."""

    station_id: str
    fuel_type: str
    ticks: list[int] = field(default_factory=list)
    demand: list[float] = field(default_factory=list)
    multiplier: list[float] = field(default_factory=list)


class SeasonalProfileModel:
    """Seasonal profile model with hourly baselines, residual quantiles, EWMA level,

    and event-aware multiplier scheduling.
    """

    name: str = "seasonal"
    version: str = "builtin"

    def __init__(
        self,
        tick_minutes: int = 15,
        ewma_alpha: float = 0.3,
        level_window: int = 8,
        coverage_calibration: float = 1.15,
    ) -> None:
        self.tick_minutes = tick_minutes
        self.ewma_alpha = ewma_alpha
        self.level_window = level_window
        self.coverage_calibration = coverage_calibration
        # base[(station_id, fuel_type, hour)] -> float
        self.base: dict[tuple[str, str, int], float] = {}
        # residual quantiles per (station_id, fuel_type)
        self.q10: dict[tuple[str, str], float] = {}
        self.q90: dict[tuple[str, str], float] = {}
        self.default_q10: float = -0.15
        self.default_q90: float = 0.15

    def fit(self, histories: list[SeriesHistory]) -> "SeasonalProfileModel":
        """Fits hourly baselines and residual quantiles from historical series observations.

        base[(s, f, hour)] = median(demand / multiplier) per hour-of-day.
        residual quantiles q10, q90 of (actual / pred - 1).
        """
        hourly_norm_demands: dict[tuple[str, str, int], list[float]] = {}
        series_residuals: dict[tuple[str, str], list[float]] = {}
        all_residuals: list[float] = []

        for h in histories:
            mult_len = len(h.multiplier)
            for i, t in enumerate(h.ticks):
                d = h.demand[i]
                m = h.multiplier[i] if i < mult_len and h.multiplier[i] > 0 else 1.0
                norm_d = d / max(1e-6, m)
                hour = (t * self.tick_minutes // 60) % 24
                hourly_key = (h.station_id, h.fuel_type, hour)
                hourly_norm_demands.setdefault(hourly_key, []).append(norm_d)

        for (sid, ftype, hour), values in hourly_norm_demands.items():
            if values:
                self.base[(sid, ftype, hour)] = float(statistics.median(values))

        # Second pass: compute residuals (actual / pred - 1)
        for h in histories:
            key_sf = (h.station_id, h.fuel_type)
            mult_len = len(h.multiplier)
            for i, t in enumerate(h.ticks):
                d = h.demand[i]
                m = h.multiplier[i] if i < mult_len and h.multiplier[i] > 0 else 1.0
                hour = (t * self.tick_minutes // 60) % 24
                b = self.base.get((h.station_id, h.fuel_type, hour))
                if b is not None and b > 0:
                    pred = b * m
                    res = (d / max(1e-6, pred)) - 1.0
                    series_residuals.setdefault(key_sf, []).append(res)
                    all_residuals.append(res)

        def _quantile(sorted_vals: list[float], q: float) -> float:
            if not sorted_vals:
                return 0.0
            if len(sorted_vals) == 1:
                return sorted_vals[0]
            idx = q * (len(sorted_vals) - 1)
            low = int(idx)
            high = min(low + 1, len(sorted_vals) - 1)
            weight = idx - low
            return sorted_vals[low] * (1.0 - weight) + sorted_vals[high] * weight

        for key_sf, res_list in series_residuals.items():
            if len(res_list) >= 4:
                res_sorted = sorted(res_list)
                self.q10[key_sf] = _quantile(res_sorted, 0.10)
                self.q90[key_sf] = _quantile(res_sorted, 0.90)
            else:
                self.q10[key_sf] = self.default_q10
                self.q90[key_sf] = self.default_q90

        if len(all_residuals) >= 4:
            all_sorted = sorted(all_residuals)
            self.default_q10 = _quantile(all_sorted, 0.10)
            self.default_q90 = _quantile(all_sorted, 0.90)

        return self

    def predict(
        self,
        h: SeriesHistory,
        start_tick: int,
        horizon: int,
        multiplier_now: float,
        multiplier_schedule: list[tuple[int, int, float]] = (),
    ) -> dict[str, list[float]]:
        """Predicts p10, p50, and p90 for the next `horizon` ticks starting from `start_tick + 1`.

        Returns:
            {"tick": [...], "p10": [...], "p50": [...], "p90": [...]}
        Guarantees:
            All values finite, >= 0, and p10 <= p50 <= p90.
        """
        key_sf = (h.station_id, h.fuel_type)
        # Check whether any baseline exists for this series
        has_baseline = any(k[0] == h.station_id and k[1] == h.fuel_type for k in self.base)

        # 1. Compute level adaptation via EWMA over last level_window observations
        level = 1.0
        n_obs = len(h.ticks)
        if has_baseline and n_obs > 0:
            window_start = max(0, n_obs - self.level_window)
            ratios: list[float] = []
            mult_len = len(h.multiplier)
            for i in range(window_start, n_obs):
                obs_t = h.ticks[i]
                obs_d = h.demand[i]
                obs_m = h.multiplier[i] if i < mult_len and h.multiplier[i] > 0 else 1.0
                obs_h = (obs_t * self.tick_minutes // 60) % 24
                b = self.base.get((h.station_id, h.fuel_type, obs_h))
                if b is not None and b > 0:
                    expected = b * obs_m
                    if expected > 0:
                        ratios.append(obs_d / expected)

            if ratios:
                ewma = 1.0
                for r in ratios:
                    ewma = self.ewma_alpha * r + (1.0 - self.ewma_alpha) * ewma
                level = max(0.1, min(10.0, ewma))

        ticks_out: list[int] = []
        p10_out: list[float] = []
        p50_out: list[float] = []
        p90_out: list[float] = []

        q10_val = self.q10.get(key_sf, self.default_q10) * self.coverage_calibration
        q90_val = self.q90.get(key_sf, self.default_q90) * self.coverage_calibration

        # Precompute schedule bounds if present
        sched_bounds = [(from_t, to_t, mult) for from_t, to_t, mult in multiplier_schedule]
        max_sched_to = max((to_t for _, to_t, _ in sched_bounds), default=-1)

        for step in range(1, horizon + 1):
            target_tick = start_tick + step
            ticks_out.append(target_tick)

            # Determine multiplier at target_tick
            mult_t: float | None = None
            for from_t, to_t, s_mult in sched_bounds:
                if from_t <= target_tick < to_t:
                    mult_t = s_mult
                    break

            if mult_t is None:
                if sched_bounds:
                    if target_tick >= max_sched_to:
                        mult_t = 1.0
                    else:
                        mult_t = multiplier_now
                else:
                    mult_t = multiplier_now

            # Determine base demand for hour
            hour = (target_tick * self.tick_minutes // 60) % 24
            b = self.base.get((h.station_id, h.fuel_type, hour))
            if b is None:
                if n_obs > 0:
                    recent_d = h.demand[-4:]
                    mult_len = len(h.multiplier)
                    recent_m = [
                        h.multiplier[k] if k < mult_len and h.multiplier[k] > 0 else 1.0
                        for k in range(max(0, n_obs - 4), n_obs)
                    ]
                    b = sum(d / max(1e-6, m) for d, m in zip(recent_d, recent_m, strict=True)) / len(recent_d)
                else:
                    b = 0.0

            p50 = max(0.0, float(b * mult_t * level))
            p10 = max(0.0, float(p50 * (1.0 + q10_val)))
            p90 = max(p50, float(p50 * (1.0 + q90_val)))
            if p10 > p50:
                p10 = p50

            p10_out.append(round(p10, 2))
            p50_out.append(round(p50, 2))
            p90_out.append(round(p90, 2))

        return {
            "tick": [float(t) for t in ticks_out],
            "p10": p10_out,
            "p50": p50_out,
            "p90": p90_out,
        }
