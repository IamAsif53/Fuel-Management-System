"""Unit tests for Autopilot ≤7k L subset selection when volume is the only gate."""

from __future__ import annotations

from types import SimpleNamespace

from app.config import EngineSettings
from app.decision.engine import DecisionEngine


def _engine() -> DecisionEngine:
    settings = EngineSettings()
    # Avoid wiring real DB for pure helper checks
    return DecisionEngine(session_factory=None, publisher=None, settings=settings)


def test_blocked_only_by_volume_detects_four_eyes_and_line_ceiling():
    assert DecisionEngine._blocked_only_by_volume(
        ["Decision volume (23,000 L) exceeds 7,000 L threshold (requires four-eyes signoff)"]
    )
    assert DecisionEngine._blocked_only_by_volume(
        [
            "Allocation line (8,000 L) exceeds 7,000 L maximum autonomous ceiling",
            "Decision volume (9,000 L) exceeds 7,000 L threshold (requires four-eyes signoff)",
        ]
    )
    assert not DecisionEngine._blocked_only_by_volume(
        [
            "Decision volume (23,000 L) exceeds 7,000 L threshold (requires four-eyes signoff)",
            "Domain SLO error budget exhausted (-10% remaining, minimum 25% required)",
        ]
    )


def test_select_autopilot_line_subset_caps_at_7000():
    eng = _engine()
    lines = [
        SimpleNamespace(quantity=6500.0),
        SimpleNamespace(quantity=4000.0),
        SimpleNamespace(quantity=3000.0),
        SimpleNamespace(quantity=8000.0),  # over per-line ceiling
    ]
    selected, total = eng._select_autopilot_line_subset(lines, max_total=7000.0)
    assert all(float(ln.quantity) <= 7000.0 for ln in selected)
    assert total <= 7000.0
    assert total == 6500.0
    assert len(selected) == 1
