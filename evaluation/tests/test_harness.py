"""Tests for evaluation harness and CLI functionality (Task 12.2 / 12.3)."""

import os
from pathlib import Path

import pytest

from evaluation.harness import EvaluationRunner, IsolatedSimClient
from evaluation.scenario_loader import load_scenario

SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "scenarios"
SIM_URL = os.environ.get("EVAL_SIM_URL", "http://localhost:8005")


def is_sim_available(url: str = SIM_URL) -> bool:
    try:
        client = IsolatedSimClient(url)
        res = client.request("/v1/health")
        return isinstance(res, dict) and res.get("status") == "ok"
    except Exception:
        return False


@pytest.fixture
def sim_client():
    if not is_sim_available():
        pytest.skip(f"Isolated simulator at {SIM_URL} is not reachable.")
    return IsolatedSimClient(SIM_URL)


def test_isolated_sim_connectivity(sim_client):
    health = sim_client.request("/v1/health")
    assert health is not None
    assert health.get("status") == "ok"


def test_runner_determinism_short():
    if not is_sim_available():
        pytest.skip(f"Isolated simulator at {SIM_URL} is not reachable.")
    runner = EvaluationRunner(sim_url=SIM_URL, out_dir=Path("evaluation/reports/test_det"))
    sc = load_scenario("baseline", SCENARIOS_DIR)
    passed = runner.verify_determinism(sc, "heuristic", ticks=10)
    assert passed is True


def test_runner_execution_and_reports():
    if not is_sim_available():
        pytest.skip(f"Isolated simulator at {SIM_URL} is not reachable.")
    out_dir = Path("evaluation/reports/test_exec")
    runner = EvaluationRunner(sim_url=SIM_URL, out_dir=out_dir)
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
