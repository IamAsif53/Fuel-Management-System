"""Phase 09 acceptance and unit tests: explanations, templates, LLM guard, assistant, F-TRUST."""
from __future__ import annotations

from fsip_common.explain import (
    build_explanation,
    check_numbers_grounded,
    render_template,
    rules_based_assistant,
)


class DummyStation:
    def __init__(self, st_id: str, inv: dict[str, float]) -> None:
        self.id = st_id
        self.name = st_id.replace("station-", "").capitalize()
        self.inventory = inv


class DummyRoute:
    def __init__(self, route_id: str, transit_ticks: int = 2) -> None:
        self.id = route_id
        self.transit_ticks = transit_ticks
        self.status = "AVAILABLE"


class DummyState:
    def __init__(self) -> None:
        self.tick = 140
        self.policy = "optimizer@1.0.0"
        self.degraded = False
        self.stations = [
            DummyStation("station-mirpur", {"DIESEL": 8400.0, "PETROL": 4000.0}),
            DummyStation("station-coxsbazar", {"DIESEL": 2500.0, "PETROL": 1000.0}),
        ]
        self.depots = []
        self.routes = [DummyRoute("route-gazipur-mirpur", transit_ticks=2)]
        self.events = []
        self.risk = {}


def test_build_explanation_schema_compliance():
    """Verify build_explanation returns all required keys matching 05 §7.1."""
    state = DummyState()
    decision = {
        "lines": [
            {
                "line_no": 1,
                "source_depot_id": "depot-gazipur",
                "destination_station_id": "station-mirpur",
                "route_id": "route-gazipur-mirpur",
                "fuel_type": "DIESEL",
                "quantity": 5000.0,
            }
        ],
        "tick": 140,
        "policy": "optimizer@1.0.0",
        "confidence": 0.81,
    }
    impact = {
        "unmet_before": 2300.0,
        "unmet_after": 150.0,
        "stations": {
            "station-mirpur:DIESEL": {
                "stockout_prob_before": 0.72,
                "stockout_prob_after": 0.19,
                "unmet_before": 2300.0,
                "unmet_after": 150.0,
            }
        },
    }

    expl = build_explanation(decision=decision, state=state, impact=impact)

    # Required keys from 05_INTELLIGENCE_DESIGN.md §7.1
    required_keys = [
        "headline",
        "why_at_risk",
        "constraints",
        "expected_impact",
        "confidence",
        "confidence_factors",
        "alternatives",
        "policy",
        "forecast_model",
        "degraded",
    ]
    for key in required_keys:
        assert key in expl, f"Missing required key: {key}"

    assert "Ship 5,000 L DIESEL" in expl["headline"]
    assert len(expl["why_at_risk"]) > 0
    assert "signal" in expl["why_at_risk"][0]
    assert "value" in expl["why_at_risk"][0]
    assert "detail" in expl["why_at_risk"][0]
    assert expl["expected_impact"]["stockout_prob_before"] == 0.72
    assert expl["expected_impact"]["stockout_prob_after"] == 0.19
    assert expl["confidence"] == 0.81
    assert len(expl["alternatives"]) >= 1


def test_templates_render_with_units_and_missing_fields():
    """Verify Jinja2 templates render with explicit units and handle missing optional fields."""
    # 1. Decision narrative
    narrative = render_template(
        "decision_narrative.j2",
        {
            "tick": 140,
            "lines": [1],
            "total_liters": 5000,
            "primary_station": "station-mirpur",
            "primary_risk_signal": "projected_stockout",
            "primary_risk_value": "6.2 h",
            "expected_impact": {
                "stockout_prob_before": 0.72,
                "stockout_prob_after": 0.19,
                "unmet_before": 2300.0,
                "unmet_after": 150.0,
            },
            "confidence": 0.81,
            "policy": "optimizer@1.0.0",
        },
    )
    assert "5000 L" in narrative or "5,000 L" in narrative
    assert "%" in narrative
    assert "tick 140" in narrative

    # 2. Alert narrative
    alert_text = render_template(
        "alert_narrative.j2",
        {
            "id": 41,
            "severity": "CRITICAL",
            "station_id": "station-coxsbazar",
            "fuel_type": "DIESEL",
            "inventory": 2500,
            "cover_hours": 3.5,
            "hours_to_stockout": 2.1,
        },
    )
    assert "CRITICAL" in alert_text
    assert "2500 L" in alert_text or "2,500 L" in alert_text
    assert "3.5 h" in alert_text
    assert "2.1 h" in alert_text

    # 3. Incident summary
    inc_text = render_template(
        "incident_summary.j2",
        {
            "id": "inc-01",
            "component": "simulator",
            "status": "resolved",
            "detected_at": "2026-11-25T00:00:00Z",
            "timeline": [{"at": "2026-11-25T00:00:00Z", "event": "Circuit breaker OPEN"}],
            "resolved_at": "2026-11-25T00:00:45Z",
            "duration_seconds": 45.0,
        },
    )
    assert "RESOLVED" in inc_text
    assert "45.0 s" in inc_text

    # 4. Shift summary
    shift_text = render_template(
        "shift_summary.j2",
        {
            "window_ticks": 96,
            "service_level": 0.995,
            "unmet_liters": 1200,
            "total_dispatched_liters": 48000,
            "decisions_count": 12,
            "autopilot_count": 10,
            "manual_count": 2,
        },
    )
    assert "99.5" in shift_text
    assert "%" in shift_text
    assert "1200 L" in shift_text or "1,200 L" in shift_text
    assert "48000 L" in shift_text or "48,000 L" in shift_text


def test_numeric_hallucination_guard():
    """Verify numeric hallucination guard accepts grounded numbers and rejects invented ones."""
    input_json = {
        "headline": "Ship 5,000 L DIESEL from depot-gazipur to station-mirpur",
        "expected_impact": {
            "stockout_prob_before": 0.72,
            "stockout_prob_after": 0.19,
            "unmet_before": 2300,
            "unmet_after": 150,
        },
        "why_at_risk": [{"signal": "projected_stockout", "value": "6.2 h"}],
    }

    # Grounded narrative (5000 L, 6.2 h, 72%, 19%, 2300 L)
    grounded_text = "Ship 5,000 L DIESEL to station-mirpur. Stockout in 6.2 h reduces from 72% to 19%, saving 2300 L."
    ok, unmatched = check_numbers_grounded(grounded_text, input_json)
    assert ok is True
    assert unmatched == []

    # Hallucinated narrative (contains invented number 9999 or 42.5)
    invented_text = "Ship 5,000 L DIESEL to station-mirpur with an extra 9999 L emergency reserve."
    ok2, unmatched2 = check_numbers_grounded(invented_text, input_json)
    assert ok2 is False
    assert "9999" in unmatched2


def test_rules_based_assistant_grounding():
    """Verify grounded assistant returns correct facts for supported query and 'not in current data' for unsupported."""
    context = {
        "state_summary": {"tick": 140, "service_level": 0.995, "unmet_liters": 100},
        "stations": [
            {"id": "station-coxsbazar", "name": "Cox's Bazar", "inventory": {"DIESEL": 2500.0, "PETROL": 1000.0}},
            {"id": "station-mirpur", "name": "Mirpur", "inventory": {"DIESEL": 8400.0, "PETROL": 4000.0}},
        ],
        "risks": [
            {
                "station_id": "station-coxsbazar",
                "fuel_type": "DIESEL",
                "level": "CRITICAL",
                "hours_to_stockout": 2.1,
                "stockout_prob_horizon": 0.85,
            }
        ],
        "alerts": [
            {"id": "41", "severity": "CRITICAL", "station_id": "station-coxsbazar", "title": "Critical Stockout Risk"}
        ],
        "decisions": [
            {"id": 1, "status": "APPROVED", "explanation": {"headline": "Ship 5,000 L DIESEL to station-mirpur"}}
        ],
        "health": {"overall": "Healthy", "engine_mode": "NORMAL"},
    }

    # 1. Supported question: "Why is Cox's Bazar at risk?"
    ans, engine, sources = rules_based_assistant("Why is Cox's Bazar at risk?", context)
    assert engine == "rules"
    assert "CRITICAL" in ans or "station-coxsbazar" in ans
    assert any(s["id"] == "station-coxsbazar" for s in sources)

    # 2. Supported question: alerts
    ans_alert, _, sources_alert = rules_based_assistant("Show me open alerts", context)
    assert "41" in ans_alert or "CRITICAL" in ans_alert
    assert any(s["id"] == "41" for s in sources_alert)

    # 3. Unsupported question: "What is the weather in Sylhet tomorrow?"
    ans_unsupported, _, sources_unsupported = rules_based_assistant("What is the weather in Sylhet tomorrow?", context)
    assert ans_unsupported == "not in current data"
    assert sources_unsupported == []
