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

    anthropic_api_key: str | None = None
    llm_model: str = "claude-haiku-5-5"
    llm_timeout_sec: float = 8.0
    breakglass_dir: str = "/data/breakglass"
    internal_alertmanager_token: str = "change-me-internal"
    demo_controls: bool = True


@lru_cache
def get_settings() -> ApiSettings:
    return ApiSettings()
