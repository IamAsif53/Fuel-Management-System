"""Shared code for engine / intelligence / api. Keep it dependency-light (pydantic, structlog only)."""
from .timeutil import expected_arrival_tick

__version__ = "0.1.0"
__all__ = ["__version__", "expected_arrival_tick"]

