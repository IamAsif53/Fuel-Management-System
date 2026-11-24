"""Explainability and LLM Ops assistant module."""
from fsip_common.explain.explainer import (
    build_explanation,
    generate_decision_narrative,
    render_template,
)
from fsip_common.explain.llm import (
    FSIP_LLM_GUARD_REJECTIONS_TOTAL,
    FSIP_LLM_LATENCY_SECONDS,
    FSIP_LLM_REQUESTS_TOTAL,
    FSIP_RECOMMENDATION_TIME_TO_DECISION,
    LLMExplainer,
    check_numbers_grounded,
    rules_based_assistant,
)

__all__ = [
    "build_explanation",
    "generate_decision_narrative",
    "render_template",
    "LLMExplainer",
    "check_numbers_grounded",
    "rules_based_assistant",
    "FSIP_LLM_REQUESTS_TOTAL",
    "FSIP_LLM_LATENCY_SECONDS",
    "FSIP_LLM_GUARD_REJECTIONS_TOTAL",
    "FSIP_RECOMMENDATION_TIME_TO_DECISION",
]
