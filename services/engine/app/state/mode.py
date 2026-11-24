"""Engine Operating Resilience Mode Manager for FSIP.

Manages transitions between resilience states:
- NORMAL: All systems healthy, simulator reachable, data fresh, optimizer solving.
- DEGRADED: Partial failure (stale data, simulator breaker OPEN, >=3 invalid cycles,
  intelligence fallback, or active degraded reasons). Autopilot is suspended;
  advisory recommendations continue if valid data is present.
  Requires all degradation reasons to clear for 2 consecutive cycles to return to NORMAL.
- SAFE_HOLD: Simulator circuit breaker OPEN for > 60 seconds.
  No planning or writes; retain last-good state; continue periodic probes.
- NO_FEASIBLE_ROUTES: All routes disrupted or unusable. Never generate invalid allocations.

Prometheus Gauge:
- fsip_engine_mode{mode="NORMAL|DEGRADED|SAFE_HOLD|NO_FEASIBLE_ROUTES"} (1 for active mode, 0 for others)
"""

from __future__ import annotations

import time
from typing import Any

from fsip_common.logging import get_logger
from prometheus_client import REGISTRY, Gauge

log = get_logger()

# ---------------------------------------------------------------- Prometheus Metric
try:
    FSIP_ENGINE_MODE = Gauge(
        "fsip_engine_mode",
        "Engine operating resilience mode (1 if active, 0 otherwise)",
        ["mode"],
    )
except ValueError:
    FSIP_ENGINE_MODE = REGISTRY._names_to_collectors.get("fsip_engine_mode")  # type: ignore[assignment]


ALL_MODES = ("NORMAL", "DEGRADED", "SAFE_HOLD", "NO_FEASIBLE_ROUTES")


class EngineModeManager:
    """Evaluates and transitions engine resilience modes according to 06 §1 Failure Matrix."""

    def __init__(self) -> None:
        self.current_mode: str = "NORMAL"
        self.clean_cycles: int = 0
        self.consecutive_invalid_cycles: int = 0
        self.last_transition_time: float = time.monotonic()
        self._update_metrics("NORMAL")

    def _update_metrics(self, mode: str) -> None:
        """Update Prometheus gauge for all 4 modes."""
        if FSIP_ENGINE_MODE is not None:
            for m in ALL_MODES:
                try:
                    FSIP_ENGINE_MODE.labels(mode=m).set(1.0 if m == mode else 0.0)
                except Exception:
                    pass

    def record_cycle_validity(self, is_valid: bool) -> None:
        """Track consecutive invalid/failed cycles."""
        if is_valid:
            self.consecutive_invalid_cycles = 0
        else:
            self.consecutive_invalid_cycles += 1

    def evaluate(
        self,
        store: Any,
        *,
        sim_breaker_open: bool = False,
        sim_breaker_open_duration: float = 0.0,
        cycle_failed: bool = False,
    ) -> str:
        """Evaluate world state and breaker telemetry to determine new engine mode.

        Returns:
            The newly evaluated engine mode string.
        """
        if cycle_failed:
            self.consecutive_invalid_cycles += 1
        else:
            self.consecutive_invalid_cycles = 0

        # 1. Check SAFE_HOLD: Simulator breaker open > 60s
        if sim_breaker_open and sim_breaker_open_duration > 60.0:
            target_mode = "SAFE_HOLD"
            self.clean_cycles = 0
            msg = f"Simulator breaker OPEN for {sim_breaker_open_duration:.1f}s (>60s)"
            return self._set_mode(target_mode, store, reason=msg)

        # 2. Check NO_FEASIBLE_ROUTES: routes populated but all are DISRUPTED
        routes = getattr(store, "routes", [])
        if routes and len(routes) > 0:
            all_disrupted = True
            for r in routes:
                status = str(getattr(r, "status", "") or (r.get("status") if isinstance(r, dict) else "")).upper()
                if status != "DISRUPTED":
                    all_disrupted = False
                    break
            if all_disrupted:
                target_mode = "NO_FEASIBLE_ROUTES"
                self.clean_cycles = 0
                return self._set_mode(target_mode, store, reason="All routes currently disrupted")

        # 3. Check DEGRADED triggers:
        # - stale flag
        # - simulator breaker OPEN (<60s)
        # - >= 3 consecutive invalid/failed cycles
        # - any reasons in store.degraded_reasons (e.g. intelligence fallback, demand_failed)
        stale = bool(getattr(store, "stale", False))
        degraded_reasons = set(getattr(store, "degraded_reasons", set()))
        invalid_cycles_trip = self.consecutive_invalid_cycles >= 3

        has_degradation = (
            stale
            or sim_breaker_open
            or invalid_cycles_trip
            or bool(degraded_reasons)
        )

        if has_degradation:
            reasons = list(degraded_reasons)
            if stale and "stale_data" not in reasons:
                reasons.append("stale_data")
            if sim_breaker_open and "sim_breaker_open" not in reasons:
                reasons.append("sim_breaker_open")
            if invalid_cycles_trip and "consecutive_invalid_cycles" not in reasons:
                reasons.append(f"{self.consecutive_invalid_cycles}_invalid_cycles")

            self.clean_cycles = 0
            return self._set_mode("DEGRADED", store, reason=", ".join(reasons))

        # 4. Clear condition: all degradation reasons absent
        self.clean_cycles += 1

        if self.current_mode != "NORMAL":
            # Requires all degradation reasons to clear for 2 consecutive cycles
            if self.clean_cycles >= 2:
                return self._set_mode("NORMAL", store, reason="All degradation conditions cleared for 2 cycles")
            else:
                # Still in recovery cycle 1: keep current mode
                return self.current_mode

        return self._set_mode("NORMAL", store, reason="Healthy")

    def _set_mode(self, new_mode: str, store: Any, reason: str = "") -> str:
        """Apply mode change, log transitions, and update metrics and store."""
        if new_mode != self.current_mode:
            old_mode = self.current_mode
            self.current_mode = new_mode
            self.last_transition_time = time.monotonic()
            log.info(
                "engine.mode_transition",
                old_mode=old_mode,
                new_mode=new_mode,
                reason=reason,
                tick=getattr(store, "tick", 0),
            )

        if store is not None:
            store.engine_mode = self.current_mode

        self._update_metrics(self.current_mode)
        return self.current_mode
