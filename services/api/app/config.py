"""Configuration settings for FSIP Operator API service."""
from __future__ import annotations

from functools import lru_cache

from fsip_common.settings import BaseServiceSettings


class ApiSettings(BaseServiceSettings):
    """API service settings, extending common BaseServiceSettings."""

    api_rate_limit_per_min: int = 120
    api_operator_token: str = "change-me-operator"
    api_jwt_secret: str = "change-me-jwt-secret"
    api_cors_origins: str = "*"

    redis_stream_name: str = "fsip:domain-events"
    redis_state_key: str = "fsip:state"
    redis_tick_key: str = "fsip:state:tick"
    redis_health_key: str = "fsip:health"


@lru_cache
def get_settings() -> ApiSettings:
    return ApiSettings()
