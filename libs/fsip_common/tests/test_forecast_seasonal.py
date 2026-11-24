"""Unit tests for shared SeasonalProfileModel.

Tests match specifications in 23_MODULE_SPECS.md §A2 and 05_INTELLIGENCE_DESIGN.md §1.2.
"""

import random

from fsip_common.forecast_seasonal import SeasonalProfileModel, SeriesHistory


def test_seasonal_profile_recovers_known_hour_factors() -> None:
    """Synthetic data with real hour factors + 10% noise over 3 days (288 ticks).

    fit + predict should recover p50 within ±5% of the true mean per hour.
    """
    daily_liters = 8500.0
    nominal_per_tick = daily_liters / 96.0

    def true_hour_factor(hour: int) -> float:
        if (7 <= hour <= 9) or (16 <= hour <= 20):
            return 1.45
        return 0.70

    # 3 days = 288 ticks, seeded RNG
    rng = random.Random(2)
    ticks: list[int] = []
    demand: list[float] = []
    multipliers: list[float] = []

    for t in range(288):
        hour = (t * 15 // 60) % 24
        factor = true_hour_factor(hour)
        noise = rng.uniform(-0.10, 0.10)
        d = nominal_per_tick * factor * (1.0 + noise)
        ticks.append(t)
        demand.append(d)
        multipliers.append(1.0)

    history = SeriesHistory(
        station_id="station-mirpur",
        fuel_type="DIESEL",
        ticks=ticks,
        demand=demand,
        multiplier=multipliers,
    )

    model = SeasonalProfileModel(tick_minutes=15)
    model.fit([history])

    # Predict next 24 hours (96 ticks) starting at tick 287
    pred = model.predict(
        history,
        start_tick=287,
        horizon=96,
        multiplier_now=1.0,
    )

    assert len(pred["tick"]) == 96
    assert len(pred["p50"]) == 96

    # Verify predictions match true mean per hour within ±5%
    for step in range(96):
        target_tick = int(pred["tick"][step])
        hour = (target_tick * 15 // 60) % 24
        expected_mean = nominal_per_tick * true_hour_factor(hour)
        predicted_p50 = pred["p50"][step]
        rel_error = abs(predicted_p50 - expected_mean) / expected_mean
        msg = f"Hour {hour} rel error {rel_error:.4f} > 0.05 (pred={predicted_p50}, exp={expected_mean})"
        assert rel_error <= 0.05, msg


def test_seasonal_multiplier_now_scaling() -> None:
    """multiplier_now=1.8 scales p50 by 1.8."""
    ticks = list(range(96))
    demand = [100.0] * 96
    multipliers = [1.0] * 96
    history = SeriesHistory("station-mirpur", "DIESEL", ticks, demand, multipliers)

    model = SeasonalProfileModel(tick_minutes=15).fit([history])

    pred_base = model.predict(history, start_tick=95, horizon=10, multiplier_now=1.0)
    pred_scaled = model.predict(history, start_tick=95, horizon=10, multiplier_now=1.8)

    for pb, ps in zip(pred_base["p50"], pred_scaled["p50"], strict=True):
        assert abs(ps - round(pb * 1.8, 2)) <= 0.05


def test_seasonal_schedule_expiration() -> None:
    """A schedule that ends at t+10 reverts to 1.0 for ticks >= t+10."""
    ticks = list(range(96))
    demand = [100.0] * 96
    multipliers = [1.0] * 96
    history = SeriesHistory("station-mirpur", "DIESEL", ticks, demand, multipliers)

    model = SeasonalProfileModel(tick_minutes=15).fit([history])
    start_t = 100
    # Schedule covers ticks 101 to 109, ends at 110
    schedule = [(101, 110, 2.0)]
    pred = model.predict(
        history,
        start_tick=start_t,
        horizon=20,
        multiplier_now=1.0,
        multiplier_schedule=schedule,
    )

    pred_unscaled = model.predict(history, start_tick=start_t, horizon=20, multiplier_now=1.0)

    for i, t in enumerate(pred["tick"]):
        tick_val = int(t)
        if tick_val < 110:
            assert abs(pred["p50"][i] - round(pred_unscaled["p50"][i] * 2.0, 2)) <= 0.05
        else:
            assert abs(pred["p50"][i] - pred_unscaled["p50"][i]) <= 0.05


def test_seasonal_empty_history_non_negative() -> None:
    """Empty history executes without error, producing non-negative, finite numbers."""
    empty_history = SeriesHistory("station-mirpur", "DIESEL", [], [], [])
    model = SeasonalProfileModel(tick_minutes=15)
    model.fit([empty_history])

    pred = model.predict(empty_history, start_tick=0, horizon=48, multiplier_now=1.0)
    assert len(pred["tick"]) == 48
    for p10, p50, p90 in zip(pred["p10"], pred["p50"], pred["p90"], strict=True):
        assert p10 >= 0.0
        assert p50 >= 0.0
        assert p90 >= 0.0
        assert p10 <= p50 <= p90


def test_seasonal_quantile_ordering_and_cold_start() -> None:
    """Quantile ordering p10 <= p50 <= p90 and cold-start when baseline is missing."""
    # 4 observations at hour 0 only
    history = SeriesHistory(
        "station-tongi",
        "PETROL",
        [0, 1, 2, 3],
        [50.0, 60.0, 70.0, 80.0],
        [1.0, 1.0, 1.0, 1.0],
    )
    model = SeasonalProfileModel(tick_minutes=15)
    # Don't fit, leaving base completely empty to test cold start fallback
    pred = model.predict(history, start_tick=10, horizon=24, multiplier_now=1.0)

    assert len(pred["p50"]) == 24
    for p10, p50, p90 in zip(pred["p10"], pred["p50"], pred["p90"], strict=True):
        assert p10 >= 0.0
        assert p10 <= p50 <= p90
        # Cold start average of [50, 60, 70, 80] = 65.0 flat
        assert abs(p50 - 65.0) <= 0.1


def test_seasonal_ewma_level_adapts_to_drift() -> None:
    """05_INTELLIGENCE_DESIGN §1.2: EWMA(alpha=0.3) over the last 8 ticks adapts to drift."""
    # Historical base: 100.0 flat
    ticks = list(range(96))
    demand = [100.0] * 96
    mults = [1.0] * 96
    history = SeriesHistory("station-mirpur", "DIESEL", ticks, demand, mults)
    model = SeasonalProfileModel(tick_minutes=15).fit([history])

    # Case A: exactly on baseline -> level == 1.0 -> p50 == 100.0
    pred_nominal = model.predict(history, start_tick=95, horizon=4, multiplier_now=1.0)
    for p50 in pred_nominal["p50"]:
        assert abs(p50 - 100.0) <= 0.01

    # Case B: sustained 30% upward drift in the last 8 ticks
    drift_demand = [100.0] * 88 + [130.0] * 8
    history_drift = SeriesHistory("station-mirpur", "DIESEL", ticks, drift_demand, mults)

    # Compute expected EWMA with alpha=0.3 over 8 consecutive 1.3 ratios
    ewma = 1.0
    for _ in range(8):
        ewma = 0.3 * 1.30 + 0.7 * ewma
    expected_p50 = round(100.0 * ewma, 2)

    pred_drift = model.predict(history_drift, start_tick=95, horizon=4, multiplier_now=1.0)
    for p50 in pred_drift["p50"]:
        assert abs(p50 - expected_p50) <= 0.05
        # Verify p50 shifted significantly above 100.0
        assert p50 > 120.0


def test_seasonal_historical_relative_residual_quantiles() -> None:
    """05_INTELLIGENCE_DESIGN §1.2: p10/p90 = p50 * (1 + q10/q90 of historical relative residuals)."""
    ticks = list(range(100))
    # Demand varies between 80 and 120 around base 100
    demand = [100.0 + (10.0 if i % 2 == 0 else -10.0) for i in range(100)]
    mults = [1.0] * 100
    history = SeriesHistory("station-mirpur", "OCTANE", ticks, demand, mults)

    # Use coverage_calibration=1.0 to check exact residual quantiles
    model = SeasonalProfileModel(tick_minutes=15, coverage_calibration=1.0).fit([history])
    key = ("station-mirpur", "OCTANE")
    assert key in model.q10
    assert key in model.q90

    # Relative residuals are (90/100 - 1) = -0.10 and (110/100 - 1) = +0.10
    assert abs(model.q10[key] - (-0.10)) <= 0.02
    assert abs(model.q90[key] - 0.10) <= 0.02

    pred = model.predict(history, start_tick=99, horizon=4, multiplier_now=1.0)
    for p10, p50, p90 in zip(pred["p10"], pred["p50"], pred["p90"], strict=True):
        assert abs(p10 - round(p50 * 0.90, 2)) <= 0.05
        assert abs(p90 - round(p50 * 1.10, 2)) <= 0.05


def test_seasonal_hourly_median_normalized_by_multiplier() -> None:
    """05_INTELLIGENCE_DESIGN §1.2: base[s,f,h] = median of (demand / multiplier)."""
    # 2 days of observations for hour 0 (ticks 0 and 96)
    # Day 1: demand = 150 with multiplier = 1.5 -> norm = 100
    # Day 2: demand = 200 with multiplier = 2.0 -> norm = 100
    # Day 3: demand = 100 with multiplier = 1.0 -> norm = 100
    ticks = [0, 96, 192]
    demand = [150.0, 200.0, 100.0]
    mults = [1.5, 2.0, 1.0]
    history = SeriesHistory("station-tongi", "DIESEL", ticks, demand, mults)

    model = SeasonalProfileModel(tick_minutes=15).fit([history])
    base_hr0 = model.base[("station-tongi", "DIESEL", 0)]
    assert abs(base_hr0 - 100.0) <= 0.01
