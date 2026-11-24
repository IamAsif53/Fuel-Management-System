"""Tests for Intelligence Service API endpoints.

Specifications:
- 05_INTELLIGENCE_DESIGN.md §1.4
- 21_API_CONTRACTS.md §4
- PHASE_05_FORECASTING.md Task 5.5
"""

import time

from app.main import app
from fastapi.testclient import TestClient


def test_intelligence_health_and_models() -> None:
    """Verifies live, ready, models listing, and model activation rollback."""
    with TestClient(app) as client:
        # 1. Health live
        res_live = client.get("/health/live")
        assert res_live.status_code == 200
        assert res_live.json()["status"] == "ok"

        # 2. Health ready
        res_ready = client.get("/health/ready")
        assert res_ready.status_code == 200
        assert "model" in res_ready.json()

        # 3. Models listing
        res_models = client.get("/models")
        assert res_models.status_code == 200
        models_data = res_models.json()
        assert "active" in models_data
        assert "available" in models_data

        # 4. Activate model (lightgbm@1.0.0)
        res_act = client.post("/models/activate", json={"name": "lightgbm", "version": "1.0.0"})
        assert res_act.status_code in (200, 404)  # 200 if artifact present, 404 if missing

        # 5. Rollback to seasonal
        res_rollback = client.post("/models/activate", json={"name": "seasonal", "version": "builtin"})
        assert res_rollback.status_code == 200
        assert client.get("/health/ready").json()["model"]["name"] == "seasonal"

        # 6. Nonexistent model returns 404
        res_fake = client.post("/models/activate", json={"name": "nonexistent", "version": "9.9.9"})
        assert res_fake.status_code == 404


def test_forecast_endpoint_and_performance() -> None:
    """Verifies /forecast contract, monotonicity, and p95 latency < 150 ms."""
    stations = ["station-mirpur", "station-tongi", "station-karnaphuli", "station-coxsbazar"]
    fuels = ["DIESEL", "PETROL", "OCTANE"]

    series_req = []
    for sid in stations:
        for f in fuels:
            history = [{"tick": t, "demand": 80.0, "multiplier": 1.0} for t in range(50, 96)]
            series_req.append(
                {
                    "station_id": sid,
                    "fuel_type": f,
                    "history": history,
                    "multiplier_now": 1.0,
                    "multiplier_schedule": [{"from_tick": 105, "to_tick": 115, "multiplier": 1.8}],
                }
            )

    payload = {
        "tick": 95,
        "tick_minutes": 15,
        "horizon": 48,
        "series": series_req,
    }

    with TestClient(app) as client:
        start_t = time.perf_counter()
        res = client.post("/forecast", json=payload)
        elapsed_ms = (time.perf_counter() - start_t) * 1000

        assert res.status_code == 200
        data = res.json()
        assert "model" in data
        assert "series" in data
        assert len(data["series"]) == 12

        # Verify points contract and monotonicity
        for s in data["series"]:
            assert len(s["points"]) == 48
            for pt in s["points"]:
                assert pt["p10"] >= 0.0
                assert pt["p50"] >= 0.0
                assert pt["p90"] >= 0.0
                assert pt["p10"] <= pt["p50"] <= pt["p90"]

        # Performance target: p95 latency < 150 ms
        assert elapsed_ms < 150.0, f"Forecast took {elapsed_ms:.2f} ms > 150 ms"


def test_anomaly_score_endpoint() -> None:
    """Verifies /anomaly/score math and z-score anomaly flagging."""
    points = [
        # Normal observation: actual close to p50
        {
            "station_id": "station-mirpur",
            "fuel_type": "DIESEL",
            "tick": 100,
            "actual": 85.0,
            "p10": 70.0,
            "p50": 85.0,
            "p90": 100.0,
        },
        # Extreme spike anomaly: actual = 200, sigma = (100 - 70)/2.56 = 11.72 -> z = (200 - 85)/11.72 = 9.81 > 3
        {
            "station_id": "station-mirpur",
            "fuel_type": "DIESEL",
            "tick": 101,
            "actual": 200.0,
            "p10": 70.0,
            "p50": 85.0,
            "p90": 100.0,
        },
    ]

    with TestClient(app) as client:
        res = client.post("/anomaly/score", json={"points": points})
        assert res.status_code == 200
        scores = res.json()["scores"]
        assert len(scores) == 2

        assert scores[0]["is_anomaly"] is False
        assert abs(scores[0]["z"]) < 0.1

        assert scores[1]["is_anomaly"] is True
        assert scores[1]["z"] > 3.0
