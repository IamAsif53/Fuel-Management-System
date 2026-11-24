"""Monte Carlo stockout risk assessment module for FSIP Engine.

Implements Task 6.2:
- Vectorized Monte Carlo simulation (N=300 samples, numpy, seeded per tick/series)
- Asymmetric normal demand sampling from p10/p50/p90 with correlated level uncertainty
- Exact locked risk thresholds:
  - CRITICAL: P(stockout <= L) >= 0.5 OR hours_to_stockout <= lead_time_hours OR station OUTAGE
  - HIGH:     P(stockout <= H) >= 0.5 OR hours_to_stockout <= lead_time_hours + 4h
  - MEDIUM:   P(stockout <= H) >= 0.2 OR fill including in-flight < 0.35
  - LOW:      otherwise
- Explanatory risk drivers
- Performance budget: full risk computation <= 50 ms for 12 series, exporting fsip_risk_duration_seconds
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from fsip_common.logging import get_logger
from fsip_common.models import Route, Station
from prometheus_client import REGISTRY, Histogram

from app.risk.projection import (
    DepotFuelProjection,
    estimate_demand,
    project_depot_fuel,
)

logger = get_logger()

# ---------------------------------------------------------------- Prometheus Metrics
try:
    FSIP_RISK_DURATION_SECONDS = Histogram(
        "fsip_risk_duration_seconds",
        "Duration of risk assessment calculation in seconds",
        buckets=(0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5),
    )
except ValueError:
    FSIP_RISK_DURATION_SECONDS = REGISTRY._names_to_collectors.get("fsip_risk_duration_seconds")  # type: ignore[assignment]


N_SAMPLES = 300
HORIZON_TICKS = 48
TICK_MINUTES = 15
FUELS = ("DIESEL", "PETROL", "OCTANE")


def make_seed(sim_run_id: int | str | None, tick: int, station_id: str, fuel_type: str) -> int:
    """Generate deterministic, reproducible seed derived from (sim_run_id, tick, station_id, fuel_type)."""
    key = f"{sim_run_id or 0}:{tick}:{station_id}:{fuel_type}"
    h = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(h[:4], "big")


@dataclass
class RiskResult:
    """Risk assessment outcome for a single station and fuel combination."""

    station_id: str
    fuel_type: str
    tick: int
    risk_level: str  # LOW | MEDIUM | HIGH | CRITICAL
    hours_to_stockout: float | None
    stockout_prob_lead: float
    stockout_prob_horizon: float
    inventory: float
    inflight: float
    capacity: float
    fill_pct: float
    fill_pct_with_inflight: float
    cover_hours: float | None
    lead_ticks: int
    lead_time_hours: float | None
    drivers: list[str] = field(default_factory=list)
    band_p10: list[float] = field(default_factory=list)
    band_p50: list[float] = field(default_factory=list)
    band_p90: list[float] = field(default_factory=list)
    demand_p50: list[float] = field(default_factory=list)
    forecast_model: str = "seasonal@builtin"

    def to_compact_dict(self) -> dict[str, Any]:
        """Compact dictionary representation for fsip:state and persistence."""
        return {
            "level": self.risk_level,
            "hours_to_stockout": self.hours_to_stockout,
            "stockout_prob_lead": round(self.stockout_prob_lead, 4),
            "stockout_prob_horizon": round(self.stockout_prob_horizon, 4),
            "inventory": round(self.inventory, 2),
            "inflight": round(self.inflight, 2),
            "fill_pct": round(self.fill_pct, 4),
            "fill_pct_with_inflight": round(self.fill_pct_with_inflight, 4),
            "cover_hours": self.cover_hours,
            "drivers": self.drivers,
            "forecast_model": self.forecast_model,
        }


def determine_risk_level(
    p_stockout_lead: float,
    p_stockout_horizon: float,
    hours_to_stockout: float | None,
    lead_time_hours: float | None,
    fill_pct_with_inflight: float,
    is_outage: bool,
) -> str:
    """Evaluate locked risk thresholds per 20_FINAL_SPEC_LOCK & 05_INTELLIGENCE_DESIGN §2.3.

    CRITICAL: P(stockout <= L) >= 0.5 OR hours_to_stockout <= lead_time_hours OR station OUTAGE
    HIGH:     P(stockout <= H) >= 0.5 OR hours_to_stockout <= lead_time_hours + 4h
    MEDIUM:   P(stockout <= H) >= 0.2 OR fill including in-flight < 0.35
    LOW:      otherwise
    """
    if is_outage:
        return "CRITICAL"
    if p_stockout_lead >= 0.5:
        return "CRITICAL"
    if hours_to_stockout is not None and lead_time_hours is not None:
        if hours_to_stockout <= lead_time_hours:
            return "CRITICAL"

    if p_stockout_horizon >= 0.5:
        return "HIGH"
    if hours_to_stockout is not None:
        threshold = (lead_time_hours + 4.0) if lead_time_hours is not None else 4.0
        if hours_to_stockout <= threshold:
            return "HIGH"

    if p_stockout_horizon >= 0.2:
        return "MEDIUM"
    if fill_pct_with_inflight < 0.35:
        return "MEDIUM"

    return "LOW"


def simulate_stockout_mc(
    inv0: float,
    capacity: float,
    p10: np.ndarray,
    p50: np.ndarray,
    p90: np.ndarray,
    arrivals: np.ndarray,
    lead_ticks: int,
    n: int = N_SAMPLES,
    seed: int = 0,
    outage: np.ndarray | None = None,
) -> dict[str, Any]:
    """Vectorized Monte Carlo simulation across N samples over horizon H.

    Reproduces reference/blueprints/analytics.py stockout_probability() with inventory band tracking.
    """
    H = len(p50)
    rng = np.random.default_rng(seed)
    z = rng.standard_normal((n, H))

    sig_hi = np.maximum(p90 - p50, 1e-6) / 1.2816
    sig_lo = np.maximum(p50 - p10, 1e-6) / 1.2816
    d = p50 + np.where(z > 0, z * sig_hi, z * sig_lo)
    d *= rng.normal(1.0, 0.05, size=(n, 1))  # Correlated level uncertainty
    d = np.clip(d, 0, None)

    if outage is not None:
        d = np.where(outage[None, :], 0.0, d)

    inv = np.full(n, float(inv0))
    first_out = np.full(n, H + 1)
    inv_history = np.zeros((n, H))

    for k in range(H):
        inv = np.minimum(capacity, inv + arrivals[k]) - d[:, k]
        hit = (inv <= 0) & (first_out > H)
        first_out[hit] = k
        inv = np.maximum(inv, 0)
        inv_history[:, k] = inv

    # Trajectory percentiles computed in a single vectorized pass
    bands = np.percentile(inv_history, (10, 50, 90), axis=0).round(2)
    band_p10 = bands[0].tolist()
    band_p50 = bands[1].tolist()
    band_p90 = bands[2].tolist()

    p_lead = float((first_out <= lead_ticks).mean())
    p_horizon = float((first_out <= H).mean())

    # Deterministic p50 path
    inv_p = float(inv0)
    t50: int | None = None
    for k in range(H):
        step_d = 0.0 if (outage is not None and outage[k]) else float(p50[k])
        inv_p = min(capacity, inv_p + arrivals[k]) - step_d
        if inv_p <= 0 and t50 is None and step_d > 0:
            t50 = k
        inv_p = max(inv_p, 0)

    hours_to_stockout = round(t50 * (TICK_MINUTES / 60.0), 2) if t50 is not None else None

    return {
        "p_stockout_lead": p_lead,
        "p_stockout_horizon": p_horizon,
        "hours_to_stockout": hours_to_stockout,
        "band_p10": band_p10,
        "band_p50": band_p50,
        "band_p90": band_p90,
    }


def assess_station_fuel(
    station: Station | dict[str, Any],
    fuel_type: str,
    routes: list[Route | dict[str, Any]],
    current_tick: int,
    sim_run_id: int | str | None,
    demand_history: Any | None,
    all_inflight: list[Any],
    forecast_bundle: Any | None = None,
    events: list[Any] | None = None,
    depot_projections: dict[tuple[str, str], DepotFuelProjection] | None = None,
) -> RiskResult:
    """Assess stockout risk and metrics for a single station and fuel combination."""
    st_id = station["id"] if isinstance(station, dict) else station.id
    st_status = station.get("status", "OPEN") if isinstance(station, dict) else station.status
    is_outage = (st_status == "OUTAGE")

    def _get_val(obj: Any, key: str) -> float:
        if isinstance(obj, dict):
            return float(obj.get(key, 0.0))
        return float(getattr(obj, key, 0.0))

    if isinstance(station, dict):
        capacity = _get_val(station.get("capacity", {}), fuel_type)
        inventory = _get_val(station.get("inventory", {}), fuel_type)
    else:
        capacity = _get_val(station.capacity, fuel_type)
        inventory = _get_val(station.inventory, fuel_type)

    # 1. Lead time calculation: min available route transit + 1 decision tick + 2 buffer ticks
    routes_to_st = [
        r for r in routes
        if (r["destination_station_id"] if isinstance(r, dict) else r.destination_station_id) == st_id
        and (r.get("status") if isinstance(r, dict) else r.status) == "AVAILABLE"
    ]
    if routes_to_st:
        min_transit = min(
            (r["transit_ticks"] if isinstance(r, dict) else r.transit_ticks)
            for r in routes_to_st
        )
        lead_ticks = min_transit + 1 + 2
        lead_time_hours: float | None = round(lead_ticks * (TICK_MINUTES / 60.0), 2)
        has_available_route = True
    else:
        lead_ticks = HORIZON_TICKS
        lead_time_hours = None
        has_available_route = False

    # 2. In-flight arrivals grouped by horizon offset k = 0..H-1
    arrivals = np.zeros(HORIZON_TICKS)
    total_inflight = 0.0
    for a in all_inflight:
        dest_id = a.destination_station_id if hasattr(a, "destination_station_id") else a.get("destination_station_id")
        f_type = a.fuel_type if hasattr(a, "fuel_type") else a.get("fuel_type")
        status = a.status if hasattr(a, "status") else a.get("status")
        arr_tick = a.expected_arrival_tick if hasattr(a, "expected_arrival_tick") else a.get("expected_arrival_tick")
        qty = float(a.quantity if hasattr(a, "quantity") else a.get("quantity", 0.0))

        if dest_id == st_id and f_type == fuel_type and status in ("PENDING", "IN_TRANSIT"):
            total_inflight += qty
            if arr_tick is not None:
                offset = arr_tick - current_tick
                if 0 <= offset < HORIZON_TICKS:
                    arrivals[offset] += qty

    # 3. Forecast distribution
    model_label = "seasonal@builtin"
    if forecast_bundle is not None:
        p50_list = forecast_bundle.get_trajectory(st_id, fuel_type, HORIZON_TICKS)
        has_quantiles = hasattr(forecast_bundle, "get_trajectory_quantile")
        p10_list = (
            forecast_bundle.get_trajectory_quantile(st_id, fuel_type, HORIZON_TICKS, 0.10)
            if has_quantiles else None
        )
        p90_list = (
            forecast_bundle.get_trajectory_quantile(st_id, fuel_type, HORIZON_TICKS, 0.90)
            if has_quantiles else None
        )
        model_label = getattr(forecast_bundle, "forecaster_label", "seasonal@builtin")

        fallback_d = estimate_demand(demand_history, capacity)
        p50 = np.array(p50_list, dtype=float) if p50_list else np.full(HORIZON_TICKS, fallback_d)
        p10 = np.array(p10_list, dtype=float) if p10_list else p50 * 0.90
        p90 = np.array(p90_list, dtype=float) if p90_list else p50 * 1.10
    else:
        est_d = estimate_demand(demand_history, capacity)
        p50 = np.full(HORIZON_TICKS, est_d)
        p10 = p50 * 0.90
        p90 = p50 * 1.10

    # Outage mask across horizon
    outage_mask = np.full(HORIZON_TICKS, is_outage)

    # 4. Monte Carlo simulation
    seed = make_seed(sim_run_id, current_tick, st_id, fuel_type)
    mc_res = simulate_stockout_mc(
        inv0=inventory,
        capacity=capacity,
        p10=p10,
        p50=p50,
        p90=p90,
        arrivals=arrivals,
        lead_ticks=lead_ticks,
        n=N_SAMPLES,
        seed=seed,
        outage=outage_mask,
    )

    fill_pct = min(1.0, max(0.0, inventory / capacity)) if capacity > 0 else 0.0
    fill_pct_with_inflight = min(1.0, max(0.0, (inventory + total_inflight) / capacity)) if capacity > 0 else 0.0

    # Calculate cover hours: (inventory + inflight) / average hourly demand
    avg_tick_demand = float(np.mean(p50)) if len(p50) > 0 else 1.0
    hourly_demand = avg_tick_demand * (60.0 / TICK_MINUTES)
    if hourly_demand > 0.0:
        cover_hours: float | None = round((inventory + total_inflight) / hourly_demand, 2)
    else:
        cover_hours = None

    # 5. Risk level evaluation
    hours_to_stockout = mc_res["hours_to_stockout"]
    p_lead = mc_res["p_stockout_lead"]
    p_horizon = mc_res["p_stockout_horizon"]

    risk_level = determine_risk_level(
        p_stockout_lead=p_lead,
        p_stockout_horizon=p_horizon,
        hours_to_stockout=hours_to_stockout,
        lead_time_hours=lead_time_hours,
        fill_pct_with_inflight=fill_pct_with_inflight,
        is_outage=is_outage,
    )

    # 6. Drivers analysis
    drivers: list[str] = []
    if is_outage:
        drivers.append("station_outage")
    if hours_to_stockout is not None and lead_time_hours is not None and hours_to_stockout <= lead_time_hours:
        drivers.append(f"stockout_within_lead_time ({hours_to_stockout}h <= {lead_time_hours}h)")
    if p_lead >= 0.5:
        drivers.append(f"high_lead_stockout_prob ({round(p_lead * 100)}%)")
    elif p_horizon >= 0.5:
        drivers.append(f"high_horizon_stockout_prob ({round(p_horizon * 100)}%)")
    elif p_horizon >= 0.2:
        drivers.append(f"elevated_stockout_prob ({round(p_horizon * 100)}%)")

    if fill_pct_with_inflight < 0.35:
        drivers.append(f"low_fill_level ({round(fill_pct_with_inflight * 100)}%)")
    if not has_available_route:
        drivers.append("all_routes_disrupted")

    # Check for active demand multiplier spike
    if events:
        for ev in events:
            ev_type = ev.get("type") if isinstance(ev, dict) else getattr(ev, "type", None)
            ev_status = ev.get("status") if isinstance(ev, dict) else getattr(ev, "status", None)
            if ev_type == "demand_spike" and ev_status == "ACTIVE":
                drivers.append("demand_spike_active")
                break

    # Check upstream depot depletion
    if depot_projections:
        for (d_id, f_type), d_proj in depot_projections.items():
            if f_type == fuel_type and d_proj.hours_to_depletion is not None and d_proj.hours_to_depletion <= 12.0:
                drivers.append(f"upstream_depot_{d_id}_depletion_in_{d_proj.hours_to_depletion}h")

    # Check forecast spread
    p50_mean = float(np.mean(p50))
    if p50_mean > 0:
        spread = float((np.mean(p90) - np.mean(p10)) / p50_mean)
        if spread > 0.25:
            drivers.append(f"wide_forecast_spread ({round(spread * 100)}%)")

    return RiskResult(
        station_id=st_id,
        fuel_type=fuel_type,
        tick=current_tick,
        risk_level=risk_level,
        hours_to_stockout=hours_to_stockout,
        stockout_prob_lead=p_lead,
        stockout_prob_horizon=p_horizon,
        inventory=inventory,
        inflight=total_inflight,
        capacity=capacity,
        fill_pct=fill_pct,
        fill_pct_with_inflight=fill_pct_with_inflight,
        cover_hours=cover_hours,
        lead_ticks=lead_ticks,
        lead_time_hours=lead_time_hours,
        drivers=drivers,
        band_p10=mc_res["band_p10"],
        band_p50=mc_res["band_p50"],
        band_p90=mc_res["band_p90"],
        demand_p50=p50.round(2).tolist(),
        forecast_model=model_label,
    )


def assess(
    store: Any,
    forecast_bundle: Any | None = None,
) -> tuple[dict[tuple[str, str], RiskResult], dict[tuple[str, str], DepotFuelProjection]]:
    """Assess risk across all stations, fuels, and depots in WorldState.

    Module contract per 23_MODULE_SPECS.md & PHASE_06_RISK_AND_ALERTS.md:
    - Vectorized Monte Carlo across 12 station-fuel series (4 stations x 3 fuels)
    - Full computation measured under 50 ms budget, exporting fsip_risk_duration_seconds
    - Returns (station_risk_map, depot_projection_map)
    """
    start_time = time.perf_counter()

    current_tick = store.tick if hasattr(store, "tick") else 0
    sim_run_id = store.sim_run_id if hasattr(store, "sim_run_id") else None
    stations = store.stations if hasattr(store, "stations") else []
    routes = store.routes if hasattr(store, "routes") else []
    depots = store.depots if hasattr(store, "depots") else []
    supply_arrivals = store.supply if hasattr(store, "supply") else []
    all_inflight = store.inflight() if hasattr(store, "inflight") else []
    demand_map = store.demand() if hasattr(store, "demand") else {}
    events = store.events if hasattr(store, "events") else []
    fb = forecast_bundle or getattr(store, "forecast_bundle", None)

    # 1. First build depot projections to inform upstream depot depletion driver
    depot_projections: dict[tuple[str, str], DepotFuelProjection] = {}
    depot_station_map: dict[str, set[str]] = {}
    for r in routes:
        d_id = r.origin_depot_id if hasattr(r, "origin_depot_id") else r.get("origin_depot_id")
        s_id = r.destination_station_id if hasattr(r, "destination_station_id") else r.get("destination_station_id")
        if d_id and s_id:
            depot_station_map.setdefault(d_id, set()).add(s_id)

    for d in depots:
        d_id = d.id if hasattr(d, "id") else d["id"]
        for f in FUELS:
            depot_projections[(d_id, f)] = project_depot_fuel(
                depot=d,
                fuel_type=f,
                supply_arrivals=supply_arrivals,
                current_tick=current_tick,
                horizon_ticks=HORIZON_TICKS,
                tick_minutes=TICK_MINUTES,
            )

    # 2. Assess station risks
    station_risks: dict[tuple[str, str], RiskResult] = {}
    for st in stations:
        st_id = st.id if hasattr(st, "id") else st["id"]
        for f in FUELS:
            history = demand_map.get((st_id, f))
            res = assess_station_fuel(
                station=st,
                fuel_type=f,
                routes=routes,
                current_tick=current_tick,
                sim_run_id=sim_run_id,
                demand_history=history,
                all_inflight=all_inflight,
                forecast_bundle=fb,
                events=events,
                depot_projections=depot_projections,
            )
            station_risks[(st_id, f)] = res

    duration = time.perf_counter() - start_time
    if FSIP_RISK_DURATION_SECONDS is not None:
        FSIP_RISK_DURATION_SECONDS.observe(duration)

    logger.debug(
        "risk.assessment_completed",
        tick=current_tick,
        sim_run_id=sim_run_id,
        duration_ms=round(duration * 1000, 2),
        series_count=len(station_risks),
    )

    return station_risks, depot_projections
