"""Intelligence forecasting and anomaly service.

Specifications:
- 05_INTELLIGENCE_DESIGN.md §1.4
- 21_API_CONTRACTS.md §4
- PHASE_05_FORECASTING.md Task 5.5
"""

from __future__ import annotations

import json
import math
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
from fastapi import FastAPI, HTTPException, Request
from fsip_common.forecast_seasonal import SeasonalProfileModel, SeriesHistory
from fsip_common.logging import configure_logging, get_logger
from fsip_common.settings import BaseServiceSettings
from prometheus_client import Counter, Gauge, Histogram, make_asgi_app
from pydantic import BaseModel, Field

from app.models.gbm import GBMQuantileModel

log = get_logger()
settings = BaseServiceSettings()

ARTIFACTS_DIR = Path(__file__).resolve().parent.parent / "artifacts"

# Prometheus metrics
MODEL_INFO = Gauge("fsip_model_info", "Active forecast model", ["name", "version"])
FORECAST_REQUESTS = Counter("fsip_forecast_requests_total", "Forecast requests counter", ["model"])
REQUEST_DURATION = Histogram("http_request_duration_seconds", "HTTP request latency in seconds", ["path"])


class DemandHistoryItem(BaseModel):
    tick: int
    demand: float
    multiplier: float = 1.0


class MultiplierScheduleItem(BaseModel):
    from_tick: int
    to_tick: int
    multiplier: float


class ForecastSeriesRequest(BaseModel):
    station_id: str
    fuel_type: str
    history: list[DemandHistoryItem] = Field(default_factory=list)
    multiplier_now: float = 1.0
    multiplier_schedule: list[MultiplierScheduleItem] = Field(default_factory=list)


class ForecastRequest(BaseModel):
    tick: int
    tick_minutes: int = 15
    horizon: int = 48
    series: list[ForecastSeriesRequest]


class ForecastPoint(BaseModel):
    tick: int
    p10: float
    p50: float
    p90: float


class ForecastSeriesResponse(BaseModel):
    station_id: str
    fuel_type: str
    points: list[ForecastPoint]


class ForecastResponse(BaseModel):
    model: str
    version: str
    series: list[ForecastSeriesResponse]


class AnomalyPointRequest(BaseModel):
    station_id: str
    fuel_type: str
    tick: int
    actual: float
    p10: float
    p50: float
    p90: float


class AnomalyScoreRequest(BaseModel):
    points: list[AnomalyPointRequest]


class AnomalyScoreItem(BaseModel):
    station_id: str
    fuel_type: str
    tick: int
    z: float
    is_anomaly: bool


class AnomalyScoreResponse(BaseModel):
    scores: list[AnomalyScoreItem]


class ModelActivateRequest(BaseModel):
    name: str
    version: str


def load_model_instance(name: str, version: str) -> tuple[Any, dict[str, Any]]:
    """Loads a model instance and its metadata by name and version from artifacts."""
    model_dir = ARTIFACTS_DIR / name / version
    meta_path = model_dir / "meta.json"
    meta: dict[str, Any] = {"name": name, "version": version}
    if meta_path.exists():
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
        except Exception as e:
            log.warning("model.meta_load_failed", error=str(e), path=str(meta_path))

    joblib_path = model_dir / "model.joblib"
    if joblib_path.exists():
        try:
            instance = joblib.load(joblib_path)
            return instance, meta
        except Exception as e:
            log.error("model.joblib_load_failed", error=str(e), path=str(joblib_path))

    # Fallback to in-memory seasonal model
    log.info("model.fallback_seasonal", reason="artifact not loadable")
    fallback_instance = SeasonalProfileModel(tick_minutes=15)
    return fallback_instance, {"name": "seasonal", "version": "builtin", "metrics": {}}


def list_available_models() -> list[dict[str, Any]]:
    """Discovers all available model versions in the artifacts directory."""
    available: list[dict[str, Any]] = []
    if not ARTIFACTS_DIR.exists():
        return [{"name": "seasonal", "version": "builtin", "metrics": {}}]

    for meta_file in ARTIFACTS_DIR.glob("*/*/meta.json"):
        try:
            with open(meta_file, encoding="utf-8") as f:
                data = json.load(f)
                available.append(data)
        except Exception:
            continue

    if not available:
        available.append({"name": "seasonal", "version": "builtin", "metrics": {}})
    return available


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging("intelligence", settings.log_level)

    active_name = "seasonal"
    active_version = "builtin"
    active_json = ARTIFACTS_DIR / "active.json"
    if active_json.exists():
        try:
            with open(active_json, encoding="utf-8") as f:
                active_data = json.load(f)
                active_name = active_data.get("name", "seasonal")
                active_version = active_data.get("version", "builtin")
        except Exception as e:
            log.warning("active_json.read_failed", error=str(e))

    instance, meta = load_model_instance(active_name, active_version)
    app.state.active_model_instance = instance
    app.state.model = {"name": meta.get("name", active_name), "version": meta.get("version", active_version)}
    app.state.active_meta = meta

    MODEL_INFO.clear()
    MODEL_INFO.labels(app.state.model["name"], app.state.model["version"]).set(1)
    log.info("intelligence.start", model=app.state.model)
    yield


app = FastAPI(title="fsip-intelligence", version="0.1.0", lifespan=lifespan)
app.mount("/metrics", make_asgi_app())


@app.middleware("http")
async def track_latency(request: Request, call_next):
    start_t = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start_t
    REQUEST_DURATION.labels(request.url.path).observe(duration)
    return response


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok", "service": "intelligence"}


@app.get("/health/ready")
async def ready() -> dict[str, Any]:
    return {"status": "ready", "model": app.state.model}


@app.get("/models")
async def models() -> dict[str, Any]:
    return {
        "active": app.state.active_meta,
        "available": list_available_models(),
    }


@app.post("/models/activate")
async def activate_model(payload: ModelActivateRequest) -> dict[str, Any]:
    model_dir = ARTIFACTS_DIR / payload.name / payload.version
    if not model_dir.exists() and not (payload.name == "seasonal" and payload.version == "builtin"):
        raise HTTPException(status_code=404, detail="MODEL_NOT_FOUND")

    instance, meta = load_model_instance(payload.name, payload.version)
    app.state.active_model_instance = instance
    app.state.model = {"name": payload.name, "version": payload.version}
    app.state.active_meta = meta

    # Update active.json on disk
    try:
        active_json = ARTIFACTS_DIR / "active.json"
        with open(active_json, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "name": payload.name,
                    "version": payload.version,
                    "activated_at": datetime.now(UTC).isoformat(),
                    "metrics": meta.get("metrics", {}),
                },
                f,
                indent=2,
            )
    except Exception as e:
        log.warning("active_json.write_failed", error=str(e))

    MODEL_INFO.clear()
    MODEL_INFO.labels(payload.name, payload.version).set(1)
    log.info("model.activated", model=app.state.model)
    return {"active": meta}


@app.post("/forecast", response_model=ForecastResponse)
async def forecast(req: ForecastRequest) -> ForecastResponse:
    FORECAST_REQUESTS.labels(app.state.model["name"]).inc()
    model_inst = app.state.active_model_instance
    series_responses: list[ForecastSeriesResponse] = []

    for s in req.series:
        history_ticks = [h.tick for h in s.history]
        history_demands = [h.demand for h in s.history]
        history_mults = [h.multiplier for h in s.history]
        sched_tuples = [(item.from_tick, item.to_tick, item.multiplier) for item in s.multiplier_schedule]

        try:
            if isinstance(model_inst, GBMQuantileModel):
                raw_pred = model_inst.predict_series(
                    station_id=s.station_id,
                    fuel_type=s.fuel_type,
                    history_ticks=history_ticks,
                    history_demand=history_demands,
                    history_multipliers=history_mults,
                    start_tick=req.tick,
                    horizon=req.horizon,
                    multiplier_now=s.multiplier_now,
                    multiplier_schedule=sched_tuples,
                )
            else:
                sh = SeriesHistory(
                    station_id=s.station_id,
                    fuel_type=s.fuel_type,
                    ticks=history_ticks,
                    demand=history_demands,
                    multiplier=history_mults,
                )
                raw_pred = model_inst.predict(
                    sh,
                    start_tick=req.tick,
                    horizon=req.horizon,
                    multiplier_now=s.multiplier_now,
                    multiplier_schedule=sched_tuples,
                )
        except Exception as e:
            log.error("forecast.predict_failed", station=s.station_id, fuel=s.fuel_type, error=str(e))
            raise HTTPException(status_code=500, detail="BAD_MODEL_OUTPUT") from e

        # Validate outputs: finite, >= 0, p10 <= p50 <= p90
        points: list[ForecastPoint] = []
        n_points = len(raw_pred["tick"])
        for i in range(n_points):
            t_val = int(raw_pred["tick"][i])
            p10 = float(raw_pred["p10"][i])
            p50 = float(raw_pred["p50"][i])
            p90 = float(raw_pred["p90"][i])

            if (
                not math.isfinite(p10)
                or not math.isfinite(p50)
                or not math.isfinite(p90)
                or p10 < 0.0
                or p50 < 0.0
                or p90 < 0.0
                or p10 > p50
                or p50 > p90
            ):
                log.error("forecast.validation_failed", tick=t_val, p10=p10, p50=p50, p90=p90)
                raise HTTPException(status_code=500, detail="BAD_MODEL_OUTPUT")

            points.append(ForecastPoint(tick=t_val, p10=p10, p50=p50, p90=p90))

        series_responses.append(
            ForecastSeriesResponse(
                station_id=s.station_id,
                fuel_type=s.fuel_type,
                points=points,
            )
        )

    return ForecastResponse(
        model=app.state.model["name"],
        version=app.state.model["version"],
        series=series_responses,
    )


@app.post("/anomaly/score", response_model=AnomalyScoreResponse)
async def score_anomalies(req: AnomalyScoreRequest) -> AnomalyScoreResponse:
    """Scores point anomalies via standardized residuals against forecast quantiles.

    z = (actual - p50) / sigma, where sigma = max(1.0, (p90 - p10) / 2.56).
    Point anomaly: |z| > 3.0.
    """
    scores: list[AnomalyScoreItem] = []
    for p in req.points:
        sigma = max(1.0, (p.p90 - p.p10) / 2.56)
        z = (p.actual - p.p50) / sigma
        is_anomaly = abs(z) > 3.0
        scores.append(
            AnomalyScoreItem(
                station_id=p.station_id,
                fuel_type=p.fuel_type,
                tick=p.tick,
                z=round(float(z), 3),
                is_anomaly=bool(is_anomaly),
            )
        )
    return AnomalyScoreResponse(scores=scores)
