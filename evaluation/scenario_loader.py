"""Scenario validation and loading module for FSIP evaluation harness (Task 12.1).

Validates scenario configurations against simulator schema and known network topology.
Fails fast on unknown event types, malformed parameters, invalid entity IDs, or invalid windows.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

VALID_REGIONS = {"region-dhaka", "region-chattogram"}
VALID_DEPOTS = {"depot-gazipur", "depot-patiya"}
VALID_STATIONS = {"station-mirpur", "station-tongi", "station-karnaphuli", "station-coxsbazar"}
VALID_ROUTES = {
    "route-gazipur-mirpur",
    "route-gazipur-tongi",
    "route-patiya-karnaphuli",
    "route-patiya-coxsbazar",
    "route-gazipur-karnaphuli",
    "route-patiya-mirpur",
}
VALID_FUELS = {"DIESEL", "PETROL", "OCTANE"}

EventType = Literal[
    "demand_spike",
    "route_disruption",
    "station_outage",
    "depot_constraint",
    "shipment_delay",
    "supply_shortfall",
]


class ScenarioEvent(BaseModel):
    """A discrete scenario event to be injected into the simulation."""
    type: EventType
    start_tick: int = Field(ge=0, description="Start tick of the event (0-indexed)")
    duration_ticks: int = Field(ge=1, description="Event duration in ticks")
    surprise: bool = Field(default=False, description="If true, event is concealed until start_tick")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Simulator event parameters")

    @model_validator(mode="after")
    def validate_event_parameters(self) -> ScenarioEvent:
        params = self.parameters

        if self.type == "demand_spike":
            mult = params.get("multiplier", 1.5)
            if not isinstance(mult, int | float) or mult <= 0:
                raise ValueError(f"demand_spike multiplier must be positive number, got {mult}")
            if "region_ids" in params:
                for r in params["region_ids"]:
                    if r not in VALID_REGIONS:
                        raise ValueError(f"Unknown region_id in demand_spike: {r}")
            if "station_ids" in params:
                for s in params["station_ids"]:
                    if s not in VALID_STATIONS:
                        raise ValueError(f"Unknown station_id in demand_spike: {s}")

        elif self.type == "route_disruption":
            route_ids = params.get("route_ids", [])
            if not isinstance(route_ids, list) or not route_ids:
                raise ValueError("route_disruption requires a non-empty list of route_ids")
            for rid in route_ids:
                if rid not in VALID_ROUTES:
                    raise ValueError(f"Unknown route_id in route_disruption: {rid}")

        elif self.type == "station_outage":
            station_ids = params.get("station_ids", [])
            if not isinstance(station_ids, list) or not station_ids:
                raise ValueError("station_outage requires a non-empty list of station_ids")
            for sid in station_ids:
                if sid not in VALID_STATIONS:
                    raise ValueError(f"Unknown station_id in station_outage: {sid}")

        elif self.type == "depot_constraint":
            depot_ids = params.get("depot_ids", [])
            if not isinstance(depot_ids, list) or not depot_ids:
                raise ValueError("depot_constraint requires a non-empty list of depot_ids")
            for did in depot_ids:
                if did not in VALID_DEPOTS:
                    raise ValueError(f"Unknown depot_id in depot_constraint: {did}")

        elif self.type == "shipment_delay":
            delay = params.get("delay_ticks", 2)
            if not isinstance(delay, int) or delay <= 0:
                raise ValueError(f"shipment_delay delay_ticks must be positive int, got {delay}")
            if "depot_ids" in params:
                for did in params["depot_ids"]:
                    if did not in VALID_DEPOTS:
                        raise ValueError(f"Unknown depot_id in shipment_delay: {did}")
            if "fuel_types" in params:
                for f in params["fuel_types"]:
                    if f not in VALID_FUELS:
                        raise ValueError(f"Unknown fuel_type in shipment_delay: {f}")

        elif self.type == "supply_shortfall":
            factor = params.get("factor", 0.5)
            if not isinstance(factor, int | float) or not (0.0 < factor < 1.0):
                raise ValueError(f"supply_shortfall factor must be in (0, 1), got {factor}")
            if "depot_ids" in params:
                for did in params["depot_ids"]:
                    if did not in VALID_DEPOTS:
                        raise ValueError(f"Unknown depot_id in supply_shortfall: {did}")
            if "fuel_types" in params:
                for f in params["fuel_types"]:
                    if f not in VALID_FUELS:
                        raise ValueError(f"Unknown fuel_type in supply_shortfall: {f}")

        return self


class ScenarioConfig(BaseModel):
    """Complete validated configuration for an evaluation scenario."""
    name: str = Field(min_length=1, description="Scenario identifier")
    description: str = Field(default="", description="Human-readable scenario description")
    ticks: int = Field(ge=1, description="Simulation duration in ticks")
    seed: int = Field(default=12345, description="Random seed for simulation reset")
    events: list[ScenarioEvent] = Field(default_factory=list, description="Ordered scenario events")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        clean = v.strip().lower()
        if not clean:
            raise ValueError("Scenario name cannot be empty")
        return clean

    @model_validator(mode="after")
    def validate_event_windows(self) -> ScenarioConfig:
        for ev in self.events:
            if ev.start_tick >= self.ticks:
                raise ValueError(
                    f"Event start_tick {ev.start_tick} must be strictly less than scenario ticks {self.ticks}"
                )
        return self


def load_scenario(name_or_path: str | Path, scenarios_dir: Path | None = None) -> ScenarioConfig:
    """Loads and validates a scenario YAML file by name or absolute path.

    Raises:
        FileNotFoundError: If scenario file cannot be located.
        ValueError: If YAML syntax is invalid or schema validation fails.
    """
    path = Path(name_or_path)
    if not path.is_file():
        if scenarios_dir is None:
            scenarios_dir = Path(__file__).resolve().parent / "scenarios"
        candidate = scenarios_dir / f"{path.stem}.yaml"
        if not candidate.is_file():
            candidate = scenarios_dir / f"{path.stem}.yml"
        if not candidate.is_file():
            raise FileNotFoundError(
                f"Scenario '{name_or_path}' not found in {scenarios_dir}"
            )
        path = candidate

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(f"Scenario file '{path}' must contain a valid YAML dictionary mapping")

    return ScenarioConfig.model_validate(raw)


def load_all_scenarios(scenarios_dir: Path | None = None) -> dict[str, ScenarioConfig]:
    """Loads and validates all scenarios found in scenarios_dir."""
    if scenarios_dir is None:
        scenarios_dir = Path(__file__).resolve().parent / "scenarios"

    configs: dict[str, ScenarioConfig] = {}
    for p in sorted(scenarios_dir.glob("*.yaml")) + sorted(scenarios_dir.glob("*.yml")):
        cfg = load_scenario(p)
        configs[cfg.name] = cfg
    return configs
