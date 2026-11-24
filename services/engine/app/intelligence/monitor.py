"""Live forecast accuracy monitoring and drift detection for FSIP Engine.

Follows:
- 05_INTELLIGENCE_DESIGN.md §1.5
- PHASE_05_DATA_GENERATION_AND_FORECASTING.md Task 5.7
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fsip_common.logging import get_logger
from prometheus_client import REGISTRY, Gauge, Histogram
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.repo import open_incident, resolve_alert, resolve_incident, upsert_alert

if TYPE_CHECKING:
    from app.db.buffer import WriteBuffer
    from app.intelligence.client import ForecastBundle, ForecastProvider
    from app.state.store import WorldState

log = get_logger()

# -----------------------------------------------------------------------------
# Prometheus Metrics
# -----------------------------------------------------------------------------

try:
    FSIP_FORECAST_ABS_ERROR = Histogram(
        "fsip_forecast_abs_error_liters",
        "Absolute demand forecast error in liters by horizon",
        ["horizon"],
        buckets=[1.0, 5.0, 10.0, 25.0, 50.0, 100.0, 250.0, 500.0, 1000.0],
    )
except ValueError:
    FSIP_FORECAST_ABS_ERROR = REGISTRY._names_to_collectors["fsip_forecast_abs_error_liters"]  # type: ignore[assignment]

try:
    FSIP_FORECAST_INTERVAL_COVERAGE = Gauge(
        "fsip_forecast_interval_coverage",
        "Rolling 80% forecast prediction interval coverage over past 96 ticks",
    )
except ValueError:
    FSIP_FORECAST_INTERVAL_COVERAGE = REGISTRY._names_to_collectors["fsip_forecast_interval_coverage"]  # type: ignore[assignment]

try:
    FSIP_FORECAST_WAPE = Gauge(
        "fsip_forecast_wape",
        "Rolling forecast Weighted Absolute Percentage Error (WAPE) over past 96 ticks",
    )
except ValueError:
    FSIP_FORECAST_WAPE = REGISTRY._names_to_collectors["fsip_forecast_wape"]  # type: ignore[assignment]


# -----------------------------------------------------------------------------
# Evaluation Data Structures
# -----------------------------------------------------------------------------

EVALUATION_HORIZONS = (1, 4, 12)


@dataclass
class TickEvaluationRecord:
    """Aggregated evaluation metrics for a single tick across all monitored series."""

    tick: int
    total_abs_error: float
    total_demand: float
    coverage_hits: int
    coverage_count: int


class ForecastQualityMonitor:
    """Monitors live forecast accuracy and detects statistical model drift."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        write_buffer: WriteBuffer | None = None,
        backtest_wape: float = 0.0563,
        drift_multiplier: float = 2.0,
        drift_patience_ticks: int = 8,
        recovery_patience_ticks: int = 8,
        min_eval_ticks: int = 1,
        window_ticks: int = 96,
    ) -> None:
        self.session_factory = session_factory
        self.write_buffer = write_buffer
        self.backtest_wape = backtest_wape
        self.drift_multiplier = drift_multiplier
        self.drift_patience_ticks = drift_patience_ticks
        self.recovery_patience_ticks = recovery_patience_ticks
        self.min_eval_ticks = min_eval_ticks
        self.window_ticks = window_ticks

        # In-memory forecast store:
        # (made_at_tick, target_tick, station_id, fuel_type) -> (p10, p50, p90)
        self._forecast_store: dict[tuple[int, int, str, str], tuple[float, float, float]] = {}

        # Rolling history of per-tick evaluation records (up to window_ticks)
        self._rolling_window: deque[TickEvaluationRecord] = deque(maxlen=window_ticks)

        # Drift state tracking
        self.is_drifting: bool = False
        self._consecutive_drift_ticks: int = 0
        self._consecutive_recovery_ticks: int = 0
        self._active_incident_id: int | None = None
        self._last_alert_fingerprint: str | None = None

        # Tracking for testing/diagnostics
        self.recorded_evaluations: list[dict[str, Any]] = []

    def record_forecast(self, bundle: ForecastBundle) -> None:
        """Store generated forecast predictions for future realization matching."""
        made_at = bundle.made_at_tick
        for (sid, ftype), series_data in bundle.forecasts.items():
            ticks = series_data.get("tick", [])
            p10s = series_data.get("p10", [])
            p50s = series_data.get("p50", [])
            p90s = series_data.get("p90", [])

            for i, target_t in enumerate(ticks):
                self._forecast_store[(made_at, int(target_t), sid, ftype)] = (
                    float(p10s[i]),
                    float(p50s[i]),
                    float(p90s[i]),
                )

        # Evict old forecasts beyond 200 ticks behind current made_at
        min_retained_tick = made_at - 200
        keys_to_remove = [k for k in self._forecast_store if k[1] < min_retained_tick]
        for k in keys_to_remove:
            del self._forecast_store[k]

    async def evaluate_tick(
        self,
        store: WorldState,
        provider: ForecastProvider | None = None,
    ) -> dict[str, Any]:
        """Evaluate forecasts against newly realized demand at current tick."""
        cur_tick = getattr(store, "tick", 0)
        sim_run_id = getattr(store, "sim_run_id", None)
        demand_dict = getattr(store, "demand", {})

        tick_abs_error = 0.0
        tick_demand = 0.0
        tick_coverage_hits = 0
        tick_coverage_count = 0
        matched_evaluations = 0

        # Evaluate across each monitored series
        for (sid, ftype), obs_deque in demand_dict.items():
            if not obs_deque:
                continue

            # Find observation for cur_tick
            current_obs = None
            for obs in reversed(obs_deque):
                obs_tick = getattr(obs, "tick", None)
                if obs_tick == cur_tick:
                    current_obs = obs
                    break
                if obs_tick is not None and obs_tick < cur_tick:
                    # Deque is chronologically ordered; stop early if past cur_tick
                    break

            if current_obs is None:
                continue

            realized_liters = float(getattr(current_obs, "demand_liters", 0.0))

            # Check each target horizon k in (1, 4, 12)
            for k in EVALUATION_HORIZONS:
                made_at = cur_tick - k
                lookup_key = (made_at, cur_tick, sid, ftype)
                forecast = self._forecast_store.get(lookup_key)
                if forecast is None:
                    continue

                p10, p50, p90 = forecast
                abs_err = abs(realized_liters - p50)
                FSIP_FORECAST_ABS_ERROR.labels(horizon=str(k)).observe(abs_err)

                # For overall rolling WAPE and interval coverage, aggregate horizon 1
                # (1-step-ahead forecast is canonical for live drift tracking)
                if k == 1:
                    tick_abs_error += abs_err
                    tick_demand += realized_liters
                    if p10 <= realized_liters <= p90:
                        tick_coverage_hits += 1
                    tick_coverage_count += 1
                    matched_evaluations += 1

        # Add record to rolling window if any predictions were matched
        if matched_evaluations > 0:
            self._rolling_window.append(
                TickEvaluationRecord(
                    tick=cur_tick,
                    total_abs_error=tick_abs_error,
                    total_demand=tick_demand,
                    coverage_hits=tick_coverage_hits,
                    coverage_count=tick_coverage_count,
                )
            )

        # Evict records older than window_ticks
        oldest_allowed = cur_tick - self.window_ticks
        while self._rolling_window and self._rolling_window[0].tick <= oldest_allowed:
            self._rolling_window.popleft()

        # Compute rolling window metrics
        win_error = sum(r.total_abs_error for r in self._rolling_window)
        win_demand = sum(r.total_demand for r in self._rolling_window)
        win_hits = sum(r.coverage_hits for r in self._rolling_window)
        win_count = sum(r.coverage_count for r in self._rolling_window)

        rolling_wape = (win_error / win_demand) if win_demand > 0.0 else 0.0
        rolling_coverage = (win_hits / win_count) if win_count > 0 else 0.80

        FSIP_FORECAST_WAPE.set(rolling_wape)
        FSIP_FORECAST_INTERVAL_COVERAGE.set(rolling_coverage)

        # ---------------------------------------------------------------------
        # Drift Detection and Mitigation (§1.5)
        # ---------------------------------------------------------------------
        drift_threshold = self.drift_multiplier * self.backtest_wape
        eval_result = {
            "tick": cur_tick,
            "rolling_wape": round(rolling_wape, 4),
            "rolling_coverage": round(rolling_coverage, 4),
            "threshold": round(drift_threshold, 4),
            "consecutive_drift_ticks": self._consecutive_drift_ticks,
            "is_drifting": self.is_drifting,
        }

        # Check if rolling window has sufficient samples to judge drift
        if len(self._rolling_window) >= self.min_eval_ticks:
            if rolling_wape > drift_threshold:
                self._consecutive_drift_ticks += 1
                self._consecutive_recovery_ticks = 0
            else:
                self._consecutive_recovery_ticks += 1
                self._consecutive_drift_ticks = 0

            # Trigger drift transition
            if self._consecutive_drift_ticks >= self.drift_patience_ticks and not self.is_drifting:
                await self._trigger_drift(store, provider, cur_tick, sim_run_id, rolling_wape, drift_threshold)

            # Trigger recovery transition
            elif self.is_drifting and self._consecutive_recovery_ticks >= self.recovery_patience_ticks:
                await self._trigger_recovery(store, provider, cur_tick, rolling_wape, drift_threshold)

        eval_result["consecutive_drift_ticks"] = self._consecutive_drift_ticks
        eval_result["is_drifting"] = self.is_drifting
        self.recorded_evaluations.append(eval_result)
        return eval_result

    async def _trigger_drift(
        self,
        store: WorldState,
        provider: ForecastProvider | None,
        cur_tick: int,
        sim_run_id: int | None,
        rolling_wape: float,
        drift_threshold: float,
    ) -> None:
        """Trigger model drift alert, incident, and fallback forecaster switch."""
        self.is_drifting = True
        log.warning(
            "forecast.model_drift_detected",
            cur_tick=cur_tick,
            rolling_wape=rolling_wape,
            drift_threshold=drift_threshold,
            consecutive_ticks=self._consecutive_drift_ticks,
        )

        # Switch forecasting provider to local seasonal fallback
        if provider:
            provider.set_drift_fallback(True)
        store.degraded_reasons.add("model_drift_fallback")

        fp = f"alert:model_drift:{sim_run_id if sim_run_id is not None else 0}"
        self._last_alert_fingerprint = fp

        alert_data = {
            "sim_run_id": sim_run_id,
            "fingerprint": fp,
            "category": "MODEL_DRIFT",
            "severity": "WARNING",
            "title": "Forecast Model Drift Detected",
            "message": (
                f"Rolling forecast WAPE ({rolling_wape:.4f}) exceeded 2x backtest threshold "
                f"({drift_threshold:.4f}) for {self.drift_patience_ticks} consecutive ticks. "
                "Switched to seasonal fallback model."
            ),
            "status": "OPEN",
            "opened_tick": cur_tick,
            "data": {
                "rolling_wape": rolling_wape,
                "drift_threshold": drift_threshold,
                "consecutive_ticks": self._consecutive_drift_ticks,
            },
        }

        # Persist alert and incident if session factory available
        if self.session_factory:
            try:
                async with self.session_factory() as session:
                    async with session.begin():
                        await upsert_alert(session, alert_data)
                        inc = await open_incident(
                            session,
                            kind="MODEL_DRIFT",
                            component="forecast",
                            summary=(
                                f"Model forecast drift detected (WAPE={rolling_wape:.4f} > "
                                f"{drift_threshold:.4f}). Switched to seasonal fallback model."
                            ),
                        )
                        self._active_incident_id = inc.id
                log.info("forecast.model_drift_alert_persisted", fingerprint=fp, incident_id=self._active_incident_id)
            except Exception as exc:
                log.warning("forecast.model_drift_persist_failed", error=str(exc))

    async def _trigger_recovery(
        self,
        store: WorldState,
        provider: ForecastProvider | None,
        cur_tick: int,
        rolling_wape: float,
        drift_threshold: float,
    ) -> None:
        """Resolve model drift state and safely resume active model forecasting."""
        self.is_drifting = False
        log.info(
            "forecast.model_drift_recovered",
            cur_tick=cur_tick,
            rolling_wape=rolling_wape,
            drift_threshold=drift_threshold,
            consecutive_recovery_ticks=self._consecutive_recovery_ticks,
        )

        # Switch forecasting provider back to remote model
        if provider:
            provider.set_drift_fallback(False)
        store.degraded_reasons.discard("model_drift_fallback")

        # Resolve alert and incident in database
        if self.session_factory and self._last_alert_fingerprint:
            try:
                async with self.session_factory() as session:
                    async with session.begin():
                        await resolve_alert(
                            session,
                            fingerprint=self._last_alert_fingerprint,
                            resolved_tick=cur_tick,
                        )
                        if self._active_incident_id is not None:
                            await resolve_incident(
                                session,
                                incident_id=self._active_incident_id,
                                summary=f"Forecast model drift recovered at tick {cur_tick} (WAPE={rolling_wape:.4f}).",
                            )
                            self._active_incident_id = None
                log.info("forecast.model_drift_resolved_in_db", fingerprint=self._last_alert_fingerprint)
            except Exception as exc:
                log.warning("forecast.model_drift_resolve_failed", error=str(exc))
