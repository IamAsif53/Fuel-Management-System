"""Tests for evaluation scenario loading and validation (Task 12.1)."""

from pathlib import Path

import pytest

from evaluation.scenario_loader import ScenarioConfig, load_all_scenarios, load_scenario

SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "scenarios"


def test_load_all_required_scenarios():
    scenarios = load_all_scenarios(SCENARIOS_DIR)
    expected_names = {
        "baseline",
        "baseline_long",
        "demand_spike",
        "supply_disruption",
        "final_combined",
        "combined_crisis",
        "regional_blackout",
        "surprise_combined",
    }
    missing = expected_names - set(scenarios.keys())
    assert expected_names.issubset(set(scenarios.keys())), f"Missing scenarios: {missing}"
    assert scenarios["baseline"].ticks == 288
    assert scenarios["baseline_long"].ticks == 600
    assert scenarios["final_combined"].seed == 9001
    assert len(scenarios["combined_crisis"].events) == 5
    assert len(scenarios["regional_blackout"].events) == 1
    assert all(ev.surprise for ev in scenarios["surprise_combined"].events)


def test_fail_fast_on_unknown_scenario():
    with pytest.raises(FileNotFoundError):
        load_scenario("non_existent_scenario", SCENARIOS_DIR)


def test_fail_fast_on_malformed_event_type():
    with pytest.raises(ValueError, match="Input should be"):
        ScenarioConfig.model_validate({
            "name": "bad_type",
            "ticks": 100,
            "events": [{"type": "alien_invasion", "start_tick": 10, "duration_ticks": 5}],
        })


def test_fail_fast_on_invalid_route():
    with pytest.raises(ValueError, match="Unknown route_id"):
        ScenarioConfig.model_validate({
            "name": "bad_route",
            "ticks": 100,
            "events": [{
                "type": "route_disruption",
                "start_tick": 10,
                "duration_ticks": 5,
                "parameters": {"route_ids": ["route-moon-base"]},
            }],
        })


def test_fail_fast_on_event_start_past_duration():
    with pytest.raises(ValueError, match="must be strictly less than scenario ticks"):
        ScenarioConfig.model_validate({
            "name": "out_of_bounds",
            "ticks": 50,
            "events": [{
                "type": "demand_spike",
                "start_tick": 60,
                "duration_ticks": 5,
                "parameters": {"multiplier": 1.5},
            }],
        })
