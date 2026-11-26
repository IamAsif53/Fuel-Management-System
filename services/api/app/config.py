"""Configuration settings for FSIP Operator API service."""
from __future__ import annotations

from functools import lru_cache

from fsip_common.settings import BaseServiceSettings
from pydantic import AliasChoices, Field


class ApiSettings(BaseServiceSettings):
    """API service settings, extending common BaseServiceSettings."""

    api_rate_limit_per_min: int = 120
    # Removed insecure "change-me-operator" static fallback (DEF-003b / DEF-001).
    api_operator_token: str = ""
    api_jwt_secret: str = Field(
        default="fsip-local-test-jwt-secret-override-in-env",
        validation_alias=AliasChoices("JWT_SECRET", "API_JWT_SECRET"),
    )
    jwt_expire_seconds: int = 28800
    demo_users: str = Field(
        default="",
        validation_alias=AliasChoices("DEMO_USERS", "API_AUTH_USERS"),
    )
    auth_users_json: str = ""
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
    cors_origins: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173",
        validation_alias=AliasChoices("CORS_ORIGINS", "API_CORS_ORIGINS"),
    )


@lru_cache
def get_settings() -> ApiSettings:
    return ApiSettings()
