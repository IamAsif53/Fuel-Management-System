"""Routers package for FSIP API."""
from .entities import router as entities_router
from .state import router as state_router
from .stream import router as stream_router
from .system import router as system_router

__all__ = [
    "entities_router",
    "state_router",
    "stream_router",
    "system_router",
]
