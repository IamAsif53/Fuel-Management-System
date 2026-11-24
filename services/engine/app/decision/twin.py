"""Shadow Digital Twin Module (Task 8.1).

Re-exports shared twin definitions from fsip_common.twin.
"""
from __future__ import annotations

from fsip_common.twin import (
    DAILY_LITERS,
    FUELS,
    ShadowTwin,
    TwinDepotProjection,
    TwinSimulationResult,
    TwinStationProjection,
)

__all__ = [
    "DAILY_LITERS",
    "FUELS",
    "ShadowTwin",
    "TwinDepotProjection",
    "TwinSimulationResult",
    "TwinStationProjection",
]
