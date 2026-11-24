"""Demand Anomaly and CUSUM Shift Detection Module (Tasks 7.1 & 7.2).

Per PHASE_07_ANOMALY_DETECTION.md & 05_INTELLIGENCE_DESIGN.md:
- Point anomaly: |z| > 3.0 where sigma = (p90 - p10) / 2.56 with floor = 1.0.
- Persistent shift: Two-sided per-(station, fuel) CUSUM with k=0.5, h=5.0.
- Event correlation: Correlates with active demand_spike events.
  - Explained anomaly -> DEMAND_ANOMALY (INFO) with 'explained by event #N'
  - Unexplained anomaly -> DEMAND_ANOMALY (WARNING) with measured percentage shift.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fsip_common.logging import get_logger
from prometheus_client import REGISTRY, Counter

logger = get_logger()

# ---------------------------------------------------------------- Prometheus Metrics
try:
    FSIP_ANOMALIES_DETECTED_TOTAL = Counter(
        "fsip_anomalies_detected_total",
        "Total anomalies detected by category",
        ["type"],
    )
except ValueError:
    FSIP_ANOMALIES_DETECTED_TOTAL = REGISTRY._names_to_collectors.get(  # type: ignore[assignment]
        "fsip_anomalies_detected_total"
    )


@dataclass
class AnomalyResult:
    """Detection result for a single demand observation."""

    station_id: str
    fuel_type: str
    tick: int
    actual_demand: float
    expected_p50: float
    z_score: float
    is_point_anomaly: bool
    is_cusum_shift: bool
    cusum_val: float
    is_explained: bool
    explaining_event_id: int | None
    severity: str  # INFO | WARNING
    message: str
    fingerprint: str


def explain_anomaly(
    station_id: str,
    fuel_type: str,
    region_id: str | None,
    tick: int,
    events: list[Any] | None,
) -> tuple[bool, int | None, str]:
    """Correlate anomaly with active or recent simulator events.

    Matching rules:
    - Event type is 'demand_spike'
    - Event is active at current tick: start_tick <= tick <= end_tick
    - Station matches event station filter OR region matches region filter
    - Empty station/region filter means event applies to all targets.
    """
    if not events:
        return False, None, "unexplained demand anomaly"

    for ev in events:
        ev_type = getattr(ev, "type", None) or (ev.get("type") if isinstance(ev, dict) else None)
        if ev_type != "demand_spike":
            continue

        start_tick = getattr(ev, "start_tick", 0) if hasattr(ev, "start_tick") else ev.get("start_tick", 0)
        end_tick = getattr(ev, "end_tick", 999999) if hasattr(ev, "end_tick") else ev.get("end_tick", 999999)

        if not (start_tick <= tick <= end_tick):
            continue

        params = getattr(ev, "parameters", {}) if hasattr(ev, "parameters") else ev.get("parameters", {})
        if not isinstance(params, dict):
            params = {}

        st_filter = params.get("station_ids") or params.get("station_id")
        reg_filter = params.get("region_ids") or params.get("region_id")
        fuel_filter = params.get("fuel_type") or params.get("fuels")

        # Check fuel filter if specified
        if fuel_filter:
            if isinstance(fuel_filter, list) and fuel_type not in fuel_filter:
                continue
            if isinstance(fuel_filter, str) and fuel_type != fuel_filter:
                continue

        # Check station match
        station_match = False
        if not st_filter and not reg_filter:
            station_match = True  # Empty means all targets
        else:
            if st_filter:
                if isinstance(st_filter, list) and station_id in st_filter:
                    station_match = True
                elif isinstance(st_filter, str) and station_id == st_filter:
                    station_match = True

            if reg_filter and region_id:
                if isinstance(reg_filter, list) and region_id in reg_filter:
                    station_match = True
                elif isinstance(reg_filter, str) and region_id == reg_filter:
                    station_match = True

        if station_match:
            ev_id = getattr(ev, "id", None) or (ev.get("id") if isinstance(ev, dict) else None)
            return True, ev_id, f"explained by event #{ev_id}"

    return False, None, "unexplained demand shift"


class DemandAnomalyDetector:
    """Stateful detector for demand point anomalies and persistent CUSUM shifts."""

    def __init__(self, k: float = 0.5, h: float = 5.0, sigma_floor: float = 1.0) -> None:
        self.k = k
        self.h = h
        self.sigma_floor = sigma_floor
        self.sim_run_id: int | None = None
        self.cusum_pos: dict[tuple[str, str], float] = {}
        self.cusum_neg: dict[tuple[str, str], float] = {}
        self.forecast_cache: dict[tuple[str, str, int], dict[str, float]] = {}

    def reset(self, sim_run_id: int | None = None) -> None:
        """Reset internal CUSUM counters on simulator reset or run transition."""
        self.sim_run_id = sim_run_id
        self.cusum_pos.clear()
        self.cusum_neg.clear()
        self.forecast_cache.clear()
        logger.info("detect.demand_anomaly_detector_reset", sim_run_id=sim_run_id)

    def record_prior_forecast(
        self,
        station_id: str,
        fuel_type: str,
        target_tick: int,
        p10: float,
        p50: float,
        p90: float,
    ) -> None:
        """Store forecast from prior origin to prevent look-ahead bias."""
        self.forecast_cache[(station_id, fuel_type, target_tick)] = {
            "p10": float(p10),
            "p50": float(p50),
            "p90": float(p90),
        }

    def update(
        self,
        station_id: str,
        fuel_type: str,
        tick: int,
        actual_demand: float,
        prior_forecast: dict[str, float] | None = None,
        region_id: str | None = None,
        events: list[Any] | None = None,
    ) -> AnomalyResult | None:
        """Process a realized demand observation and detect anomalies.

        Returns AnomalyResult if an anomaly or CUSUM shift is detected, else None.
        """
        key = (station_id, fuel_type)

        fc = prior_forecast or self.forecast_cache.get((station_id, fuel_type, tick))
        if fc is None:
            # Fallback nominal baseline if no forecast cached
            p50 = 250.0
            p10 = 200.0
            p90 = 300.0
        else:
            p10 = float(fc.get("p10", 200.0))
            p50 = float(fc.get("p50", 250.0))
            p90 = float(fc.get("p90", 300.0))

        # Spread sigma: (p90 - p10) / 2.56 with floor
        sigma = max(self.sigma_floor, (p90 - p10) / 2.56)
        residual = actual_demand - p50
        z_score = residual / sigma

        # 1. Point anomaly: |z| > 3.0
        is_point = abs(z_score) > 3.0

        # 2. Two-sided CUSUM
        s = z_score
        c_pos = max(0.0, self.cusum_pos.get(key, 0.0) + s - self.k)
        c_neg = max(0.0, self.cusum_neg.get(key, 0.0) - s - self.k)
        self.cusum_pos[key] = c_pos
        self.cusum_neg[key] = c_neg

        is_shift = (c_pos > self.h) or (c_neg > self.h)
        cusum_val = c_pos if c_pos > self.h else c_neg

        if not is_point and not is_shift:
            return None

        # 3. Event Correlation
        is_explained, ev_id, exp_msg = explain_anomaly(
            station_id=station_id,
            fuel_type=fuel_type,
            region_id=region_id,
            tick=tick,
            events=events,
        )

        pct_shift = round(((actual_demand - p50) / max(1.0, p50)) * 100, 1)

        if is_explained:
            severity = "INFO"
            message = f"{fuel_type} actual {actual_demand:.1f} L vs p50 {p50:.1f} L " f"({pct_shift:+}%): {exp_msg}"
        else:
            severity = "WARNING"
            message = (
                f"{fuel_type} actual {actual_demand:.1f} L vs p50 {p50:.1f} L "
                f"({pct_shift:+}%, z={z_score:.2f}, CUSUM={cusum_val:.1f})"
            )

        fingerprint = f"station:{station_id}:{fuel_type}:demand_anomaly"

        if FSIP_ANOMALIES_DETECTED_TOTAL is not None:
            FSIP_ANOMALIES_DETECTED_TOTAL.labels(type="demand").inc()

        return AnomalyResult(
            station_id=station_id,
            fuel_type=fuel_type,
            tick=tick,
            actual_demand=actual_demand,
            expected_p50=p50,
            z_score=z_score,
            is_point_anomaly=is_point,
            is_cusum_shift=is_shift,
            cusum_val=cusum_val,
            is_explained=is_explained,
            explaining_event_id=ev_id,
            severity=severity,
            message=message,
            fingerprint=fingerprint,
        )
