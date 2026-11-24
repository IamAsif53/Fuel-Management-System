from functools import lru_cache

from fsip_common.settings import BaseServiceSettings


class EngineSettings(BaseServiceSettings):
    engine_mode: str = "advisory"            # advisory | autopilot
    engine_policy: str = "optimizer"         # optimizer | heuristic
    auto_migrate: bool = True
    engine_decision_interval_ticks: int = 1
    engine_poll_interval_sec: float = 1.0
    sim_timeout_sec: float = 2.0
    sim_max_retries: int = 3
    breaker_failure_threshold: int = 5
    breaker_reset_sec: float = 15.0
    intelligence_timeout_sec: float = 1.5
    auto_approve_max_liters: float = 7000
    auto_approve_min_confidence: float = 0.7
    depot_reserve_fraction: float = 0.0
    station_target_fill: float = 0.90
    planning_horizon_ticks: int = 96
    safety_ticks: int = 16
    safety_noise: float = 0.15
    safety_weight: float = 2.0
    scarcity_policy: str = "history_based"
    anthropic_api_key: str | None = None
    llm_model: str = "claude-haiku-5-5"
    llm_timeout_sec: float = 8.0
    breakglass_dir: str = "/data/breakglass"


@lru_cache
def get_settings() -> EngineSettings:
    return EngineSettings()
