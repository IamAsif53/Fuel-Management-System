"""Routers package for FSIP API."""
from .decisions import router as decisions_router
from .entities import router as entities_router
from .risk_alerts import router as risk_alerts_router
from .state import router as state_router
from .stream import router as stream_router
from .system import router as system_router

__all__ = [
    "decisions_router",
    "entities_router",
    "risk_alerts_router",
    "state_router",
    "stream_router",
    "system_router",
]
