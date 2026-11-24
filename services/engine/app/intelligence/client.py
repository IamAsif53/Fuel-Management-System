"""Forecast client and fallback provider for FSIP Engine.

Communicates with fsip-intelligence via /forecast with timeout and circuit breaker protection.
Falls back to built-in SeasonalProfileModel on timeout, connection error, or breaker trip.
Specifications:
- 05_INTELLIGENCE_DESIGN.md §1.2, §1.4, §8
- PHASE_05_FORECASTING.md Task 5.6
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
from fsip_common.forecast_seasonal import SeasonalProfileModel, SeriesHistory
from fsip_common.logging import get_logger
from fsip_common.models import DemandObservation, SimEvent, Station
from prometheus_client import REGISTRY, Counter, Histogram

from app.sim.circuit import CircuitBreaker

if TYPE_CHECKING:
    from app.state.store import WorldState

log = get_logger()

# Metrics
try:
    FALLBACK_ACTIVATIONS = Counter(
        "fsip_fallback_activations_total",
        "Number of times fallback components were activated",
        ["component"],
    )
except ValueError:
    FALLBACK_ACTIVATIONS = REGISTRY._names_to_collectors.get("fsip_fallback_activations_total")  # type: ignore[assignment]

try:
    FORECAST_LATENCY = Histogram(
        "fsip_forecast_client_latency_seconds",
        "Latency of forecast requests to intelligence service in seconds",
    )
except ValueError:
    FORECAST_LATENCY = REGISTRY._names_to_collectors.get("fsip_forecast_client_latency_seconds")  # type: ignore[assignment]

TARGET_DB_HORIZONS = [1, 4, 12, 48]
FUELS = ["DIESEL", "PETROL", "OCTANE"]


@dataclass
class ForecastBundle:
    """Contains multi-horizon quantile forecast predictions for all network series."""

    model_name: str
    model_version: str
    is_fallback: bool
    made_at_tick: int
    forecasts: dict[tuple[str, str], dict[str, list[float]]] = field(default_factory=dict)

    def get_p50(self, station_id: str, fuel_type: str, target_tick: int) -> float | None:
        """Retrieves p50 forecast for a given station, fuel, and target tick."""
        series_data = self.forecasts.get((station_id, fuel_type))
        if not series_data or "tick" not in series_data:
            return None
        ticks = series_data["tick"]
        if target_tick in ticks:
            idx = ticks.index(target_tick)
            return series_data["p50"][idx]
        return None

    def get_trajectory(self, station_id: str, fuel_type: str, horizon: int = 96) -> list[float]:
        """Returns sequence of p50 predictions up to horizon ticks.

        If horizon exceeds available forecast points, repeats the last available point.
        """
        series_data = self.forecasts.get((station_id, fuel_type))
        if not series_data or "p50" not in series_data or not series_data["p50"]:
            return [0.0] * horizon

        p50_vals = series_data["p50"]
        if len(p50_vals) >= horizon:
            return list(p50_vals[:horizon])

        last_val = p50_vals[-1]
        padded = list(p50_vals) + [last_val] * (horizon - len(p50_vals))
        return padded

    def to_db_rows(
        self,
        sim_run_id: int | None,
        sim_time: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Extracts rows for target ticks t+1, t+4, t+12, t+48 only, bounding storage growth."""
        now_dt = sim_time or datetime.now(UTC)
        rows: list[dict[str, Any]] = []

        for (sid, ftype), series_data in self.forecasts.items():
            ticks = series_data.get("tick", [])
            tick_to_idx = {int(t): i for i, t in enumerate(ticks)}

            for offset in TARGET_DB_HORIZONS:
                t_target = self.made_at_tick + offset
                if t_target in tick_to_idx:
                    idx = tick_to_idx[t_target]
                    rows.append(
                        {
                            "sim_run_id": sim_run_id,
                            "made_at_tick": self.made_at_tick,
                            "station_id": sid,
                            "fuel_type": ftype,
                            "target_tick": t_target,
                            "p10": float(series_data["p10"][idx]),
                            "p50": float(series_data["p50"][idx]),
                            "p90": float(series_data["p90"][idx]),
                            "model_name": self.model_name,
                            "model_version": self.model_version,
                            "is_fallback": self.is_fallback,
                            "sim_time": now_dt,
                        }
                    )
        return rows


class ForecastProvider:
    """Orchestrates intelligence forecast retrieval with breaker protection and local fallback."""

    def __init__(
        self,
        base_url: str = "http://intelligence:8200",
        timeout_sec: float = 1.5,
        breaker: CircuitBreaker | None = None,
        fallback_model: SeasonalProfileModel | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec
        self.breaker = breaker or CircuitBreaker(
            name="intelligence",
            failure_threshold=5,
            reset_timeout=15.0,
        )
        self.fallback_model = fallback_model or SeasonalProfileModel(tick_minutes=15)

    async def get_forecast(self, store: WorldState, horizon: int = 48) -> ForecastBundle:
        """Retrieves forecasts from intelligence service or executes local seasonal fallback."""
        cur_tick = getattr(store, "tick", 0)
        tick_minutes = getattr(store, "tick_minutes", 15)

        # Check circuit breaker
        if not self.breaker.allow():
            log.warning("forecast.breaker_open", name=self.breaker.name)
            store.degraded_reasons.add("prediction_service_down")
            store.degraded_reasons.add("intelligence_breaker_open")
            FALLBACK_ACTIVATIONS.labels(component="forecast").inc()
            return self._execute_fallback(store, cur_tick, horizon)

        # Prepare request payload from WorldState
        payload = self._build_request_payload(store, cur_tick, tick_minutes, horizon)

        try:
            with FORECAST_LATENCY.time():
                async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
                    resp = await client.post(f"{self.base_url}/forecast", json=payload)

            if resp.status_code != 200:
                raise RuntimeError(f"Intelligence service returned HTTP {resp.status_code}: {resp.text}")

            data = resp.json()
            bundle = self._parse_response(data, cur_tick)
            self.breaker.record_success()
            store.degraded_reasons.discard("prediction_service_down")
            store.degraded_reasons.discard("intelligence_breaker_open")
            store.forecaster_label = f"{bundle.model_name}@{bundle.model_version}"
            return bundle

        except Exception as exc:
            log.warning("forecast.call_failed", error=str(exc))
            self.breaker.record_failure()
            store.degraded_reasons.add("prediction_service_down")
            if self.breaker.state == CircuitBreaker.OPEN:
                store.degraded_reasons.add("intelligence_breaker_open")
            FALLBACK_ACTIVATIONS.labels(component="forecast").inc()
            return self._execute_fallback(store, cur_tick, horizon)

    def _build_request_payload(
        self,
        store: WorldState,
        cur_tick: int,
        tick_minutes: int,
        horizon: int,
    ) -> dict[str, Any]:
        """Assembles the /forecast request body from current WorldState."""
        stations = getattr(store, "stations", {})
        if isinstance(stations, dict):
            station_list = list(stations.values())
        else:
            station_list = list(stations)

        events: list[SimEvent] = getattr(store, "events", [])
        active_scheduled_spikes = [
            ev
            for ev in events
            if getattr(ev, "event_type", None) == "demand_spike"
            and getattr(ev, "status", None) in ("ACTIVE", "SCHEDULED")
        ]

        series_requests: list[dict[str, Any]] = []

        for st in station_list:
            sid = st.id if isinstance(st, Station) else st.get("id")
            s_mult = float(st.demand_multiplier if isinstance(st, Station) else st.get("demand_multiplier", 1.0))

            # Extract schedule intervals affecting this station
            s_schedules: list[dict[str, Any]] = []
            for ev in active_scheduled_spikes:
                target_sid = getattr(ev, "target_station_id", None) or getattr(ev, "station_id", None)
                target_reg = getattr(ev, "target_region_id", None) or getattr(ev, "region_id", None)
                st_reg = st.region_id if isinstance(st, Station) else st.get("region_id")

                if (target_sid == sid) or (target_reg == st_reg) or (not target_sid and not target_reg):
                    from_t = int(getattr(ev, "start_tick", cur_tick))
                    dur = int(getattr(ev, "duration_ticks", 12))
                    to_t = int(getattr(ev, "end_tick", from_t + dur))
                    mult_val = float(getattr(ev, "multiplier", 1.5))
                    s_schedules.append({"from_tick": from_t, "to_tick": to_t, "multiplier": mult_val})

            for fuel in FUELS:
                key = (sid, fuel)
                dem_deque = store.demand.get(key)
                hist_rows: list[dict[str, Any]] = []
                if dem_deque:
                    # Take last up to 192 observations
                    raw_slice = list(dem_deque)[-192:]
                    for item in raw_slice:
                        if isinstance(item, DemandObservation):
                            hist_rows.append(
                                {
                                    "tick": item.tick,
                                    "demand": float(item.demand_liters),
                                    "multiplier": float(getattr(item, "demand_multiplier", 1.0)),
                                }
                            )
                        elif isinstance(item, dict):
                            hist_rows.append(
                                {
                                    "tick": int(item["tick"]),
                                    "demand": float(item["demand_liters"]),
                                    "multiplier": float(item.get("demand_multiplier", 1.0)),
                                }
                            )

                series_requests.append(
                    {
                        "station_id": sid,
                        "fuel_type": fuel,
                        "history": hist_rows,
                        "multiplier_now": s_mult,
                        "multiplier_schedule": s_schedules,
                    }
                )

        return {
            "tick": cur_tick,
            "tick_minutes": tick_minutes,
            "horizon": horizon,
            "series": series_requests,
        }

    def _parse_response(self, data: dict[str, Any], cur_tick: int) -> ForecastBundle:
        """Parses /forecast response into a ForecastBundle."""
        model_name = data.get("model", "lightgbm")
        version = data.get("version", "1.0.0")
        series_map: dict[tuple[str, str], dict[str, list[float]]] = {}

        for s_resp in data.get("series", []):
            sid = s_resp["station_id"]
            ftype = s_resp["fuel_type"]
            ticks: list[float] = []
            p10s: list[float] = []
            p50s: list[float] = []
            p90s: list[float] = []

            for pt in s_resp.get("points", []):
                ticks.append(float(pt["tick"]))
                p10s.append(float(pt["p10"]))
                p50s.append(float(pt["p50"]))
                p90s.append(float(pt["p90"]))

            series_map[(sid, ftype)] = {
                "tick": ticks,
                "p10": p10s,
                "p50": p50s,
                "p90": p90s,
            }

        return ForecastBundle(
            model_name=model_name,
            model_version=version,
            is_fallback=False,
            made_at_tick=cur_tick,
            forecasts=series_map,
        )

    def _execute_fallback(self, store: WorldState, cur_tick: int, horizon: int) -> ForecastBundle:
        """Executes SeasonalProfileModel fallback fitted on the engine's own demand buffer."""
        # Fit seasonal model on store's history
        histories: list[SeriesHistory] = []
        for (sid, ftype), dem_deque in store.demand.items():
            if dem_deque:
                t_list, d_list, m_list = [], [], []
                for item in dem_deque:
                    if isinstance(item, DemandObservation):
                        t_list.append(item.tick)
                        d_list.append(float(item.demand_liters))
                        m_list.append(float(getattr(item, "demand_multiplier", 1.0)))
                    elif isinstance(item, dict):
                        t_list.append(int(item["tick"]))
                        d_list.append(float(item["demand_liters"]))
                        m_list.append(float(item.get("demand_multiplier", 1.0)))
                histories.append(
                    SeriesHistory(
                        station_id=sid,
                        fuel_type=ftype,
                        ticks=t_list,
                        demand=d_list,
                        multiplier=m_list,
                    )
                )

        if histories:
            self.fallback_model.fit(histories)

        stations = getattr(store, "stations", {})
        station_list = list(stations.values()) if isinstance(stations, dict) else list(stations)

        events: list[SimEvent] = getattr(store, "events", [])
        active_scheduled_spikes = [
            ev
            for ev in events
            if getattr(ev, "event_type", None) == "demand_spike"
            and getattr(ev, "status", None) in ("ACTIVE", "SCHEDULED")
        ]

        series_map: dict[tuple[str, str], dict[str, list[float]]] = {}
        for st in station_list:
            sid = st.id if isinstance(st, Station) else st.get("id")
            s_mult = float(st.demand_multiplier if isinstance(st, Station) else st.get("demand_multiplier", 1.0))

            sched_tuples: list[tuple[int, int, float]] = []
            for ev in active_scheduled_spikes:
                target_sid = getattr(ev, "target_station_id", None) or getattr(ev, "station_id", None)
                target_reg = getattr(ev, "target_region_id", None) or getattr(ev, "region_id", None)
                st_reg = st.region_id if isinstance(st, Station) else st.get("region_id")
                if (target_sid == sid) or (target_reg == st_reg) or (not target_sid and not target_reg):
                    from_t = int(getattr(ev, "start_tick", cur_tick))
                    dur = int(getattr(ev, "duration_ticks", 12))
                    to_t = int(getattr(ev, "end_tick", from_t + dur))
                    mult_val = float(getattr(ev, "multiplier", 1.5))
                    sched_tuples.append((from_t, to_t, mult_val))

            for fuel in FUELS:
                key = (sid, fuel)
                dem_deque = store.demand.get(key)
                t_list, d_list, m_list = [], [], []
                if dem_deque:
                    for item in dem_deque:
                        if isinstance(item, DemandObservation):
                            t_list.append(item.tick)
                            d_list.append(float(item.demand_liters))
                            m_list.append(float(getattr(item, "demand_multiplier", 1.0)))
                        elif isinstance(item, dict):
                            t_list.append(int(item["tick"]))
                            d_list.append(float(item["demand_liters"]))
                            m_list.append(float(item.get("demand_multiplier", 1.0)))

                sh = SeriesHistory(sid, fuel, t_list, d_list, m_list)
                pred = self.fallback_model.predict(
                    sh,
                    start_tick=cur_tick,
                    horizon=horizon,
                    multiplier_now=s_mult,
                    multiplier_schedule=sched_tuples,
                )
                series_map[key] = pred

        store.forecaster_label = "seasonal@builtin (fallback)"
        return ForecastBundle(
            model_name="seasonal",
            model_version="builtin",
            is_fallback=True,
            made_at_tick=cur_tick,
            forecasts=series_map,
        )
