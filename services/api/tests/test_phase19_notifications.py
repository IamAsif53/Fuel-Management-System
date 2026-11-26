"""Tests for Phase 19 Notification Escalation Service and API endpoints."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest
from app.main import app
from app.services.notifier import NotificationService


@pytest.mark.asyncio
async def test_notification_service_send_p1_alert():
    """Verify NotificationService formats MIME message and sends via SMTP."""
    service = NotificationService()
    service.enabled = True

    mock_smtp_instance = MagicMock()
    with patch("smtplib.SMTP", return_value=mock_smtp_instance):
        res = await service.send_p1_alert(
            alert_id="P1-UNIT-001",
            title="Critical Stockout Imminent at Urban Station 1",
            severity="CRITICAL",
            entity_id="st-1",
            causal_driver="Demand spike exhausted buffer; route blocked.",
            recommended_action="Dispatch 5000L emergency diesel allocation.",
            tick=150,
            recipient="operator@test.internal",
        )

        assert res["status"] == "DELIVERED"
        assert res["alert_id"] == "P1-UNIT-001"
        assert res["severity"] == "CRITICAL"
        assert mock_smtp_instance.__enter__.return_value.send_message.called


@pytest.mark.asyncio
async def test_notification_service_graceful_failure():
    """Verify SMTP connection failure is caught gracefully without throwing to caller."""
    service = NotificationService()
    service.enabled = True

    with patch("smtplib.SMTP", side_effect=ConnectionRefusedError("SMTP unreachable")):
        res = await service.send_p1_alert(
            alert_id="P1-FAIL-001",
            title="Failed delivery test",
            severity="HIGH",
            entity_id="dp-1",
            causal_driver="Testing failure resilience",
            recommended_action="Check logs",
        )

        assert res["status"] == "FAILED"
        assert "SMTP unreachable" in res["error"]


@pytest.mark.asyncio
async def test_notifications_api_test_endpoint():
    """Verify POST /api/notifications/test dispatches synthetic alert."""
    mock_smtp_instance = MagicMock()
    with patch("smtplib.SMTP", return_value=mock_smtp_instance):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            # Open mode: no Sign-In required for operator actions
            res = await client.post("/api/notifications/test")
            assert res.status_code == 200
            data = res.json()
            assert data["alert_id"] == "P1-TEST-SYNTHETIC"
            assert data["severity"] == "CRITICAL"


@pytest.mark.asyncio
async def test_notifications_api_history_endpoint():
    """Verify GET /api/notifications/history returns recent alert history."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/api/notifications/history")
        assert res.status_code == 200
        data = res.json()
        assert "total" in data
        assert isinstance(data["items"], list)
