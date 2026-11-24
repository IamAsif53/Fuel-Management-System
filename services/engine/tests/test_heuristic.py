"""Tests for services/engine/app/decision/heuristic.py.

Includes deterministic unit tests and Hypothesis property-based tests.
"""

from app.decision.constraints import check_line
from app.decision.heuristic import plan_heuristic
from hypothesis import given, settings
from hypothesis import strategies as st


def _make_heuristic_state(station_inv_override=None, depot_disp_cap=12000.0):
    inv = station_inv_override or {"DIESEL": 3000.0, "PETROL": 2500.0, "OCTANE": 1500.0}
    return {
        "tick": 10,
        "stale": False,
        "degraded": False,
        "routes": {
            "r-gazipur-mirpur": {
                "id": "r-gazipur-mirpur",
                "source_depot_id": "depot-gazipur",
                "destination_station_id": "station-mirpur",
                "status": "AVAILABLE",
                "max_shipment": 5000.0,
                "transit_ticks": 2,
            },
            "r-patiya-mirpur": {
                "id": "r-patiya-mirpur",
                "source_depot_id": "depot-patiya",
                "destination_station_id": "station-mirpur",
                "status": "AVAILABLE",
                "max_shipment": 4000.0,
                "transit_ticks": 4,
            },
        },
        "depots": [
            {
                "id": "depot-gazipur",
                "status": "OPEN",
                "inventory": {"DIESEL": 20000.0, "PETROL": 15000.0, "OCTANE": 10000.0},
                "dispatch_capacity_per_tick": depot_disp_cap,
            },
            {
                "id": "depot-patiya",
                "status": "OPEN",
                "inventory": {"DIESEL": 20000.0, "PETROL": 15000.0, "OCTANE": 10000.0},
                "dispatch_capacity_per_tick": 8000.0,
            },
        ],
        "stations": [
            {
                "id": "station-mirpur",
                "status": "OPEN",
                "capacity": {"DIESEL": 10000.0, "PETROL": 8000.0, "OCTANE": 5000.0},
                "inventory": inv,
            }
        ],
        "events": [],
        "inflight": [],
        "demand": {
            ("station-mirpur", "DIESEL"): [50.0] * 8,
            ("station-mirpur", "PETROL"): [40.0] * 8,
            ("station-mirpur", "OCTANE"): [20.0] * 8,
        },
    }


def test_station_below_reorder_fill_receives_recommendations():
    # Mirpur has DIESEL 3000/10000 = 30% (< 60% reorder fill)
    state = _make_heuristic_state()
    plan = plan_heuristic(state)

    assert len(plan.lines) > 0
    fuels_planned = {line.fuel_type for line in plan.lines}
    assert "DIESEL" in fuels_planned
    assert plan.policy == "heuristic@1.0.0"

    # All generated lines must pass check_line validation
    used_disp = {}
    for line in plan.lines:
        v = check_line(state, line, used_dispatch=used_disp)
        assert len(v) == 0
        used_disp[line.source_depot_id] = used_disp.get(line.source_depot_id, 0.0) + line.quantity


def test_critical_station_prioritized():
    # Mirpur DIESEL is at 500L / 10000L = 5% (CRITICAL)
    # PETROL is at 4500L / 8000L = 56% (MEDIUM)
    state = _make_heuristic_state(station_inv_override={"DIESEL": 500.0, "PETROL": 4500.0, "OCTANE": 4000.0})
    plan = plan_heuristic(state)

    assert len(plan.lines) > 0
    # First line should target DIESEL because it is CRITICAL
    assert plan.lines[0].fuel_type == "DIESEL"
    assert plan.lines[0].quantity > 0


def test_deterministic_repeatable_output():
    state1 = _make_heuristic_state()
    state2 = _make_heuristic_state()

    plan1 = plan_heuristic(state1)
    plan2 = plan_heuristic(state2)

    assert len(plan1.lines) == len(plan2.lines)
    for l1, l2 in zip(plan1.lines, plan2.lines, strict=True):
        assert l1.route_id == l2.route_id
        assert l1.source_depot_id == l2.source_depot_id
        assert l1.destination_station_id == l2.destination_station_id
        assert l1.fuel_type == l2.fuel_type
        assert l1.quantity == l2.quantity


def test_shared_dispatch_capacity_respected_across_fuels():
    # Set depot dispatch cap to 5000L
    # Station needs DIESEL, PETROL, OCTANE
    state = _make_heuristic_state(depot_disp_cap=5000.0)
    plan = plan_heuristic(state)

    total_depot_qty = sum(item.quantity for item in plan.lines if item.source_depot_id == "depot-gazipur")
    assert total_depot_qty <= 5000.0


def test_route_transit_ordering():
    # Gazipur has transit 2, Patiya has transit 4
    # Gazipur should be chosen before Patiya for Mirpur
    state = _make_heuristic_state()
    plan = plan_heuristic(state)
    assert any(item.route_id == "r-gazipur-mirpur" for item in plan.lines)


def test_no_recommendations_when_all_stations_full():
    # Station has 95% inventory
    state = _make_heuristic_state(station_inv_override={"DIESEL": 9500.0, "PETROL": 7600.0, "OCTANE": 4800.0})
    plan = plan_heuristic(state)
    assert len(plan.lines) == 0


# ---------------------------------------------------------------------------
# Hypothesis Property-Based Testing
# ---------------------------------------------------------------------------
@settings(max_examples=30, deadline=None)
@given(
    diesel_inv=st.floats(min_value=0.0, max_value=10000.0),
    petrol_inv=st.floats(min_value=0.0, max_value=8000.0),
    octane_inv=st.floats(min_value=0.0, max_value=5000.0),
    gazipur_diesel=st.floats(min_value=0.0, max_value=30000.0),
    dispatch_cap=st.floats(min_value=1000.0, max_value=20000.0),
)
def test_hypothesis_no_generated_lines_violate_constraints(
    diesel_inv,
    petrol_inv,
    octane_inv,
    gazipur_diesel,
    dispatch_cap,
):
    state = _make_heuristic_state(
        station_inv_override={"DIESEL": diesel_inv, "PETROL": petrol_inv, "OCTANE": octane_inv},
        depot_disp_cap=dispatch_cap,
    )
    state["depots"][0]["inventory"]["DIESEL"] = gazipur_diesel

    plan = plan_heuristic(state)

    used_disp = {}
    for line in plan.lines:
        is_crit = "CRITICAL" in line.reason
        violations = check_line(state, line, used_dispatch=used_disp, critical=is_crit)
        # Invariant: No emitted heuristic line may ever violate feasibility rules!
        assert len(violations) == 0, f"Line {line} had violations: {violations}"
        used_disp[line.source_depot_id] = used_disp.get(line.source_depot_id, 0.0) + line.quantity


def test_heuristic_prioritizes_critical_risk_results():
    """Verify heuristic prioritizes stations with CRITICAL risk_results and marks has_critical_risk."""
    from app.risk.stockout import RiskResult

    state = _make_heuristic_state(
        station_inv_override={"DIESEL": 4000.0, "PETROL": 4000.0, "OCTANE": 4000.0},
    )

    # Invalidate DIESEL at Mirpur with CRITICAL risk_results
    r_res = RiskResult(
        station_id="station-mirpur",
        fuel_type="DIESEL",
        tick=10,
        risk_level="CRITICAL",
        hours_to_stockout=2.0,
        stockout_prob_lead=0.75,
        stockout_prob_horizon=0.95,
        inventory=4000.0,
        inflight=0.0,
        capacity=10000.0,
        fill_pct=0.40,
        fill_pct_with_inflight=0.40,
        cover_hours=2.0,
        lead_ticks=5,
        lead_time_hours=1.25,
        drivers=["high_lead_stockout_prob"],
    )
    state["risk_results"] = {("station-mirpur", "DIESEL"): r_res}

    plan = plan_heuristic(state)

    assert len(plan.lines) > 0
    # First line must be the CRITICAL DIESEL line
    assert plan.lines[0].fuel_type == "DIESEL"
    assert plan.has_critical_risk is True

