"""Tests for evaluation harness and CLI functionality (Task 12.2 / 12.3)."""

from pathlib import Path

import pytest

from evaluation.harness import EvaluationRunner, IsolatedSimClient
from evaluation.scenario_loader import load_scenario

SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "scenarios"


@pytest.fixture
def sim_client():
    return IsolatedSimClient("http://localhost:8005")


def test_isolated_sim_connectivity(sim_client):
    health = sim_client.request("/v1/health")
    assert health is not None
    assert health.get("status") == "ok"


def test_runner_determinism_short():
    runner = EvaluationRunner(sim_url="http://localhost:8005", out_dir=Path("evaluation/reports/test_det"))
    sc = load_scenario("baseline", SCENARIOS_DIR)
    passed = runner.verify_determinism(sc, "heuristic", ticks=10)
    assert passed is True


def test_runner_execution_and_reports():
    out_dir = Path("evaluation/reports/test_exec")
    runner = EvaluationRunner(sim_url="http://localhost:8005", out_dir=out_dir)
    sc = load_scenario("baseline", SCENARIOS_DIR)
    res = runner.run_single(sc, "do_nothing", ticks_override=5)

    assert res["scenario"] == "baseline"
    assert res["policy"] == "do_nothing"
    assert res["ticks"] == 5

    runner.save_reports([res])
    runner.generate_plots([res])

    assert (out_dir / "results.json").is_file()
    assert (out_dir / "results.csv").is_file()
    assert (out_dir / "report.md").is_file()
