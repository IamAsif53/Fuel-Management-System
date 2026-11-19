"""Pydantic models of the BUP Fuel Supply Simulator API (v1.0.0), validated against real responses.

Rules (see 02_SIMULATOR_FACTS.md / 04_DATA_MODEL_AND_API.md §5):
- every capacity/inventory map must contain all 3 fuels, finite and >= 0
- unknown enum values are errors (-> quarantine + DATA_QUALITY alert); unknown extra fields are ignored
- sim_time accepts naive or tz-aware ISO strings
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .timeutil import parse_sim_time

FuelType = Literal["DIESEL", "PETROL", "OCTANE"]
FUELS: tuple[FuelType, ...] = ("DIESEL", "PETROL", "OCTANE")
NonNeg = Annotated[float, Field(ge=0, allow_inf_nan=False)]
EventType = Literal["demand_spike", "route_disruption", "station_outage", "depot_constraint", "shipment_delay",
                    "supply_shortfall"]
FaultType = Literal["latency", "unavailable", "error_rate", "stale_data", "stream_disconnect"]
AllocationStatus = Literal["PENDING", "IN_TRANSIT", "ARRIVED", "FAILED", "CANCELLED"]
ACTIVE_ALLOCATION_STATUSES = ("PENDING", "IN_TRANSIT")


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=False)


def _fuel_map(v: Any) -> dict[str, float]:
    if not isinstance(v, dict):
        raise ValueError("must be an object keyed by fuel type")
    missing = [f for f in FUELS if f not in v]
    if missing:
        raise ValueError(f"missing fuel keys: {missing}")
    return v


FuelMap = dict[FuelType, NonNeg]


class Health(_Base):
    status: str
    database: str | None = None
    simulation: dict[str, Any] | None = None


class Instance(_Base):
    id: int
    scenario_id: str
    scenario_version: str
    seed: int
    sim_time: datetime
    tick: int = Field(ge=0)
    tick_minutes: int = Field(gt=0)
    status: Literal["PAUSED", "RUNNING"]

    @field_validator("sim_time", mode="before")
    @classmethod
    def _parse_time(cls, v):
        return parse_sim_time(v)


class Region(_Base):
    id: str
    name: str
    demand_factor: float = Field(gt=0, allow_inf_nan=False)


class Depot(_Base):
    id: str
    name: str
    region_id: str
    status: Literal["OPEN", "CONSTRAINED"]
    dispatch_capacity_per_tick: NonNeg
    capacity: FuelMap
    inventory: FuelMap

    @field_validator("capacity", "inventory", mode="before")
    @classmethod
    def _fuel_maps(cls, v):
        return _fuel_map(v)


class Station(_Base):
    id: str
    name: str
    region_id: str
    status: Literal["OPEN", "OUTAGE"]
    demand_profile: str
    demand_multiplier: NonNeg
    capacity: FuelMap
    inventory: FuelMap

    @field_validator("capacity", "inventory", mode="before")
    @classmethod
    def _fuel_maps(cls, v):
        return _fuel_map(v)


class Route(_Base):
    id: str
    source_depot_id: str
    destination_station_id: str
    transit_ticks: int = Field(ge=1)
    max_shipment: float = Field(gt=0, allow_inf_nan=False)
    status: Literal["AVAILABLE", "DISRUPTED"]


class SupplyArrival(_Base):
    id: str
    depot_id: str
    fuel_type: FuelType
    quantity: NonNeg
    planned_tick: int
    actual_tick: int | None = None
    status: Literal["SCHEDULED", "DELAYED", "ARRIVED"]


class SimEvent(_Base):
    id: int
    type: EventType
    start_tick: int
    end_tick: int
    status: Literal["SCHEDULED", "ACTIVE", "RESOLVED"]
    parameters: dict[str, Any] = Field(default_factory=dict)

    def targets(self, key: str) -> list[str]:
        """Filter list for station_ids/region_ids/route_ids/depot_ids/fuel_types. EMPTY LIST = ALL entities."""
        return list(self.parameters.get(key) or [])

    def covers_tick(self, tick: int) -> bool:
        return self.status != "RESOLVED" and self.start_tick <= tick < self.end_tick


class Allocation(_Base):
    id: int
    idempotency_key: str
    source_depot_id: str
    destination_station_id: str
    route_id: str
    fuel_type: FuelType
    quantity: float = Field(gt=0, allow_inf_nan=False)
    created_tick: int
    departure_tick: int | None = None
    expected_arrival_tick: int | None = None
    actual_arrival_tick: int | None = None
    status: AllocationStatus
    failure_reason: str | None = None


class DemandObservation(_Base):
    id: int
    station_id: str
    fuel_type: FuelType
    tick: int
    sim_time: datetime
    demand_liters: NonNeg
    served_liters: NonNeg
    unmet_liters: NonNeg

    @field_validator("sim_time", mode="before")
    @classmethod
    def _parse_time(cls, v):
        return parse_sim_time(v)


class SimMetrics(_Base):
    served_demand_liters: NonNeg
    unmet_demand_liters: NonNeg
    service_level: float = Field(ge=0, le=1)
    allocation_liters: NonNeg
    allocation_failures: int = Field(ge=0)


class AllocationRequest(_Base):
    idempotency_key: str = Field(min_length=1, max_length=150)
    source_depot_id: str = Field(min_length=1)
    destination_station_id: str = Field(min_length=1)
    route_id: str = Field(min_length=1)
    fuel_type: FuelType
    quantity: float = Field(gt=0, allow_inf_nan=False)


# ---------------------------------------------------------------- error envelopes & admin
class SimErrorDetail(_Base):
    code: str
    message: str = ""
    data: dict[str, Any] | None = None


class SimValidationErrorItem(_Base):
    loc: list[str | int] = Field(default_factory=list)
    msg: str
    type: str
    input: Any = None
    ctx: dict[str, Any] | None = None

    @property
    def field_name(self) -> str:
        return str(self.loc[-1]) if self.loc else ""


class SimErrorEnvelope(_Base):
    """Parses all simulator error formats:
    - Domain errors: {"detail": {"code": "...", "message": "..."}}
    - Fault injected errors: {"error": {"code": "FAULT_INJECTED", "message": "..."}}
    - Stream disconnects: {"detail": {"code": "FAULT_INJECTED"}}
    - FastAPI 422 validations: {"detail": [{"loc": [...], "msg": "...", "type": "..."}]}
    - Fallback string details: {"detail": "..."}
    """
    detail: SimErrorDetail | list[SimValidationErrorItem] | str | None = None
    error: SimErrorDetail | str | None = None
    code: str = ""
    message: str = ""
    validation_errors: list[SimValidationErrorItem] | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_before(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            raise ValueError("error envelope payload must be a dict")
        if not data:
            raise ValueError("error envelope payload cannot be empty")
        return data

    @model_validator(mode="after")
    def _normalize_envelope(self) -> SimErrorEnvelope:
        if self.error is not None:
            if isinstance(self.error, SimErrorDetail):
                self.code = self.error.code
                self.message = self.error.message or ""
            else:
                self.code = self.code or "UNKNOWN"
                self.message = str(self.error)
            return self

        if self.detail is not None:
            if isinstance(self.detail, SimErrorDetail):
                self.code = self.detail.code
                self.message = self.detail.message or ""
                return self
            elif isinstance(self.detail, list):
                self.code = "VALIDATION_ERROR"
                self.validation_errors = self.detail
                msgs = []
                for item in self.detail:
                    loc_str = ".".join(str(x) for x in item.loc) if item.loc else ""
                    msgs.append(f"{loc_str}: {item.msg}" if loc_str else item.msg)
                self.message = "; ".join(msgs)
                return self
            elif isinstance(self.detail, str):
                self.code = self.code or "UNKNOWN"
                self.message = self.detail
                return self

        if self.code:
            self.message = self.message or ""
            return self

        raise ValueError("error envelope must contain 'detail', 'error', or 'code'")

    @property
    def is_fault_injected(self) -> bool:
        return self.code == "FAULT_INJECTED"

    @property
    def is_validation_error(self) -> bool:
        return self.code == "VALIDATION_ERROR" or bool(self.validation_errors)


class AdminStepResult(_Base):
    tick: int = Field(ge=0)
    sim_time: datetime

    @field_validator("sim_time", mode="before")
    @classmethod
    def _parse_time(cls, v: Any) -> datetime:
        return parse_sim_time(v)


class AdminAuditEntry(_Base):
    id: int
    timestamp: datetime
    action: str
    actor: str = "admin"
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp", mode="before")
    @classmethod
    def _parse_time(cls, v: Any) -> datetime:
        return parse_sim_time(v)


class EventCreate(_Base):
    """Body for POST /admin/events (testing / harness / demo controls only)."""
    type: EventType
    start_tick: int = Field(ge=0)
    duration_ticks: int = Field(gt=0)
    parameters: dict[str, Any] = Field(default_factory=dict)


class FaultCreate(_Base):
    """Body for POST /admin/faults (testing / chaos drills only)."""
    type: FaultType
    duration_seconds: int = Field(gt=0, le=3600)
    parameters: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------- cross-entity checks
def world_issues(depots: list[Depot], stations: list[Station], routes: list[Route],
                 regions: list[Region] | None = None, tolerance: float = 1.01) -> list[str]:
    """Return human-readable consistency problems (empty list = OK). Used by sim/validation.py."""
    issues: list[str] = []
    d_ids = {d.id for d in depots}
    s_ids = {s.id for s in stations}
    r_ids = {r.id for r in regions} if regions is not None else None
    for r in routes:
        if r.source_depot_id not in d_ids:
            issues.append(f"route {r.id}: unknown depot {r.source_depot_id}")
        if r.destination_station_id not in s_ids:
            issues.append(f"route {r.id}: unknown station {r.destination_station_id}")
    for node in [*depots, *stations]:
        if r_ids is not None and node.region_id not in r_ids:
            issues.append(f"{node.id}: unknown region {node.region_id}")
        for f in FUELS:
            if node.inventory[f] > node.capacity[f] * tolerance:
                issues.append(f"{node.id}: {f} inventory {node.inventory[f]} > capacity {node.capacity[f]}")
    return issues
